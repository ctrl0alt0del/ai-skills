"""JSON document storage with atomic publication and interprocess CAS locks.

Lock files remain in place: removing one after unlocking could let another
writer lock a different inode for the same key. OS advisory locks release on
process exit, so a crashed writer does not require unsafe stale-lock stealing.
Temporary document files are removed on all ordinary failure paths.
"""

from contextlib import contextmanager
import hashlib
import os
from pathlib import Path
import tempfile

from graph_contract import ReviewError, parse_json
from document_repositories import MAX_DOCUMENT_BYTES, document_bytes, document_key


class FilesystemDocumentStore:
    def __init__(self, path, *, max_bytes=MAX_DOCUMENT_BYTES):
        self.path = Path(path).expanduser().resolve()
        self.max_bytes = max_bytes

    def _path(self, key):
        document_key(key)
        candidate = self.path.joinpath(*key.split("/"))
        current = self.path
        for part in key.split("/"):
            current = current / part
            if current.is_symlink():
                raise ReviewError("Document paths must not follow symlinks.", 409)
        if self.path not in candidate.resolve().parents:
            raise ReviewError("Document path escapes its configured directory.")
        return candidate

    def read(self, key):
        path = self._path(key)
        try:
            # The final no-follow open also protects a swapped final path.
            descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
            with os.fdopen(descriptor, "rb") as stream:
                data = stream.read(self.max_bytes + 1)
        except FileNotFoundError as error:
            raise ReviewError("Document not found. Provision this repository explicitly first.", 404) from error
        except OSError as error:
            raise ReviewError("Cannot read the configured filesystem document.", 500) from error
        if len(data) > self.max_bytes:
            raise ReviewError("Document exceeds the configured storage size limit.", 413)
        try:
            document = parse_json(data.decode("utf-8"))
        except UnicodeError as error:
            raise ReviewError("Stored document must be UTF-8 JSON.", 409) from error
        if not isinstance(document, dict):
            raise ReviewError("Stored document must be a JSON object.", 409)
        return document, hashlib.sha256(data).hexdigest()

    @contextmanager
    def _lock(self, key):
        # Only explicit writes provision directories and lock files.
        self._path(key)
        self.path.mkdir(parents=True, exist_ok=True, mode=0o700)
        locks = self.path / ".locks"
        if locks.is_symlink():
            raise ReviewError("The repository lock directory must not be a symlink.", 409)
        locks.mkdir(exist_ok=True, mode=0o700)
        lock_path = locks / (hashlib.sha256(key.encode("utf-8")).hexdigest() + ".lock")
        descriptor = None
        unlock = None
        try:
            descriptor = os.open(lock_path, os.O_CREAT | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0), 0o600)
            if os.name == "posix":
                import fcntl
                try:
                    fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError as error:
                    raise ReviewError("Another writer holds this document. Reload before retrying.", 409) from error
                unlock = lambda: fcntl.flock(descriptor, fcntl.LOCK_UN)
            elif os.name == "nt":
                import msvcrt
                if os.fstat(descriptor).st_size == 0:
                    os.write(descriptor, b"\0")
                os.lseek(descriptor, 0, os.SEEK_SET)
                try:
                    msvcrt.locking(descriptor, msvcrt.LK_NBLCK, 1)
                except OSError as error:
                    raise ReviewError("Another writer holds this document. Reload before retrying.", 409) from error
                def release():
                    os.lseek(descriptor, 0, os.SEEK_SET)
                    msvcrt.locking(descriptor, msvcrt.LK_UNLCK, 1)
                unlock = release
            else:
                raise ReviewError("This operating system does not support the filesystem CAS lock adapter.", 501)
            yield
        except ReviewError:
            raise
        except OSError as error:
            raise ReviewError("Cannot write the configured filesystem document.", 500) from error
        finally:
            try:
                if unlock is not None:
                    unlock()
            finally:
                if descriptor is not None:
                    os.close(descriptor)

    @staticmethod
    def _sync_directory(directory):
        if os.name != "posix":
            return
        descriptor = os.open(directory, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)

    def _write(self, key, data, *, exclusive):
        path = self._path(key)
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        self._path(key)
        descriptor, temporary = tempfile.mkstemp(prefix=".document-", dir=path.parent)
        try:
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
            if exclusive:
                try:
                    os.link(temporary, path)
                except FileExistsError as error:
                    raise ReviewError("Document already exists.", 409) from error
            else:
                os.replace(temporary, path)
            self._sync_directory(path.parent)
        finally:
            Path(temporary).unlink(missing_ok=True)

    def create(self, key, document):
        data = document_bytes(document, self.max_bytes)
        with self._lock(document_key(key)):
            self._write(key, data, exclusive=True)

    def compare_and_swap(self, key, document, expected_token):
        data = document_bytes(document, self.max_bytes)
        if not isinstance(expected_token, str) or not expected_token:
            raise ReviewError("A nonempty document revision token is required.")
        def current_token():
            try:
                return self.read(key)[1]
            except ReviewError as error:
                if error.status == 404:
                    raise ReviewError("Document changed or was removed. Reload before retrying this update.", 409) from error
                raise
        # A failed CAS against missing storage must not bootstrap it.
        current_token()
        with self._lock(document_key(key)):
            if current_token() != expected_token:
                raise ReviewError("Document changed. Reload before retrying this update.", 409)
            self._write(key, data, exclusive=False)


# A conventional spelling is retained for integrations that prefer FileSystem.
FileSystemDocumentStore = FilesystemDocumentStore
