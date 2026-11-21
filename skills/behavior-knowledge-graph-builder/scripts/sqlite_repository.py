"""SQLite document adapter for independently configured graph/review storage.

Each CAS update runs under BEGIN IMMEDIATE and stores the entire document.
Reads use SQLite's read-only URI mode and never create a directory, database,
table, or review. This adapter does not use the legacy per-session schema.
"""

from contextlib import contextmanager
import os
from pathlib import Path
import sqlite3

from graph_contract import ReviewError, parse_json
from document_repositories import MAX_DOCUMENT_BYTES, document_bytes, document_key


TABLE = "behavior_review_documents"


class SQLiteDocumentStore:
    def __init__(self, path, *, max_bytes=MAX_DOCUMENT_BYTES, timeout=15):
        self.path = Path(path).expanduser().resolve()
        self.max_bytes = max_bytes
        self.timeout = timeout

    @contextmanager
    def _connection(self, *, write=False, provision=False, missing_status=404):
        if provision:
            self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            try:
                descriptor = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            except FileExistsError:
                pass
            else:
                os.close(descriptor)
        if not self.path.is_file():
            raise ReviewError("Document repository not found. Provision it explicitly first.", missing_status)
        connection = None
        try:
            uri = self.path.as_uri() + ("?mode=rw" if write else "?mode=ro")
            connection = sqlite3.connect(uri, uri=True, timeout=self.timeout, isolation_level=None)
            connection.execute(f"PRAGMA busy_timeout={int(self.timeout * 1000)}")
            yield connection
        except ReviewError:
            raise
        except sqlite3.OperationalError as error:
            if "locked" in str(error).lower() or "busy" in str(error).lower():
                raise ReviewError("The document database is busy. Reload before retrying.", 409) from error
            if "no such table" in str(error).lower():
                raise ReviewError("Document repository is not initialized.", missing_status) from error
            raise ReviewError("Cannot access the configured document database.", 500) from error
        except sqlite3.Error as error:
            raise ReviewError("Cannot access the configured document database.", 500) from error
        finally:
            if connection is not None:
                connection.close()

    def read(self, key):
        document_key(key)
        with self._connection() as connection:
            row = connection.execute(f"SELECT content, version FROM {TABLE} WHERE key=?", (key,)).fetchone()
        if row is None:
            raise ReviewError("Document not found.", 404)
        if len(row[0].encode("utf-8")) > self.max_bytes:
            raise ReviewError("Document exceeds the configured storage size limit.", 413)
        document = parse_json(row[0])
        if not isinstance(document, dict):
            raise ReviewError("Stored document must be a JSON object.", 409)
        return document, str(row[1])

    def create(self, key, document):
        document_key(key)
        content = document_bytes(document, self.max_bytes).decode("utf-8")
        with self._connection(write=True, provision=True) as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                connection.execute(f"CREATE TABLE IF NOT EXISTS {TABLE} "
                                   "(key TEXT PRIMARY KEY, content TEXT NOT NULL, version INTEGER NOT NULL CHECK(version >= 1))")
                try:
                    connection.execute(f"INSERT INTO {TABLE}(key,content,version) VALUES (?,?,1)", (key, content))
                except sqlite3.IntegrityError as error:
                    raise ReviewError("Document already exists.", 409) from error
                connection.commit()
            except BaseException:
                connection.rollback()
                raise

    def compare_and_swap(self, key, document, expected_token):
        document_key(key)
        content = document_bytes(document, self.max_bytes).decode("utf-8")
        if not isinstance(expected_token, str) or not expected_token:
            raise ReviewError("A nonempty document revision token is required.")
        with self._connection(write=True, missing_status=409) as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                row = connection.execute(f"SELECT version FROM {TABLE} WHERE key=?", (key,)).fetchone()
                if row is None:
                    raise ReviewError("Document changed or was removed. Reload before retrying this update.", 409)
                if str(row[0]) != expected_token:
                    raise ReviewError("Document changed. Reload before retrying this update.", 409)
                connection.execute(f"UPDATE {TABLE} SET content=?, version=version+1 WHERE key=?", (content, key))
                connection.commit()
            except BaseException:
                connection.rollback()
                raise
