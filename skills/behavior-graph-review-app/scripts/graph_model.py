"""Graph contract and legacy SQLite review-session persistence.

Public contract helpers remain importable here for compatibility; reusable
review actions and validation live outside the storage adapter.
"""

from contextlib import contextmanager
import hashlib
import json
import math
import os
from pathlib import Path
import secrets
import shutil
import sqlite3
import tempfile
import time
import uuid

from graph_contract import (ASPECTS, DISPOSITIONS, GRAPH_SCHEMA, ID, KINDS,
                            MAX_GRAPH_BYTES, NODE_KINDS, PROCESSING_FIELDS,
                            PROCESSING_OUTCOMES, RELATIONSHIPS, SCHEMA_VERSION,
                            TARGET_TYPES, ReviewError, advance_feedback, canonical,
                            digest, normalize_feedback, parse_json, read_graph,
                            review_digest, selected, text_value, timestamp, validate_graph)
from review_actions import ReviewActions


class Store(ReviewActions):
    def __init__(self, session):
        self.directory = Path(session).expanduser().resolve()
        self.database = self.directory / "review.sqlite3"

    def initialize(self, title):
        title = text_value(title, "title", 200)
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        if any(self.directory.iterdir()):
            raise ReviewError("Session directory is not empty. Reopen it with status/serve or choose a new directory.")
        try:
            descriptor = os.open(self.database, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            os.close(descriptor)
        except FileExistsError as error:
            raise ReviewError("This session already exists.", 409) from error
        initial = {
            "session_id": uuid.uuid4().hex, "title": title, "state_version": 1,
            "round": 0, "graph_digest": None, "graph": None, "annotations": [],
            "rounds": [], "approval": None, "review_requested": False, "finalized": None,
        }
        with self.connection() as connection:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.executescript("""
                CREATE TABLE state (id INTEGER PRIMARY KEY CHECK(id=1), content TEXT NOT NULL);
                CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
                CREATE TABLE rounds (round INTEGER PRIMARY KEY, content TEXT NOT NULL);
                CREATE TABLE audit (id INTEGER PRIMARY KEY AUTOINCREMENT, content TEXT NOT NULL);
            """)
            connection.execute("INSERT INTO state VALUES (1, ?)", (canonical(initial),))
            connection.execute("INSERT INTO meta VALUES ('csrf_token', ?)", (secrets.token_urlsafe(32),))
            connection.execute("INSERT INTO audit(content) VALUES (?)", (canonical({
                "action": "initialized", "at": timestamp(), "state_version": 1, "title": title,
            }),))
            connection.commit()
        return {**initial, "agent_active": False}

    @contextmanager
    def connection(self, timeout=15):
        if not self.database.is_file():
            raise ReviewError("Review session not found. Initialize the session first.", 404)
        connection = sqlite3.connect(str(self.database), timeout=timeout, isolation_level=None)
        connection.execute(f"PRAGMA busy_timeout={int(timeout * 1000)}")
        try:
            yield connection
        finally:
            connection.close()

    def _state(self, connection):
        row = connection.execute("SELECT content FROM state WHERE id=1").fetchone()
        if row is None:
            raise ReviewError("Session initialization is incomplete.", 409)
        state = json.loads(row[0])
        legacy_agents = {note["id"] for note in state["annotations"]
                         if "feedback_revision" not in note and note.get("source") == "agent"}
        pending = set()
        if legacy_agents:
            # Older notes lack reopen actors. Consult the audit only during
            # legacy normalization; never write or change semantic note fields.
            for audit_row in connection.execute("SELECT content FROM audit ORDER BY id"):
                event = json.loads(audit_row[0])
                if event.get("state_version", 0) > state["state_version"]:
                    continue
                payload = event.get("payload", {})
                if not isinstance(payload, dict):
                    continue
                if (event.get("source") in {"ui", "chat"}
                        or event.get("action") in {"reopened", "annotation_edited", "annotation_deleted"}):
                    identifier = payload.get("annotation_id")
                    if identifier in legacy_agents:
                        pending.add(identifier)
                if event.get("action") == "proposed":
                    for reopened in payload.get("reopened_annotations", []):
                        identifier = reopened.get("annotation_id")
                        if identifier in legacy_agents:
                            pending.add(identifier)
                if pending == legacy_agents:
                    break
        for note in state["annotations"]:
            normalize_feedback(note, note["id"] in pending)
        return state

    def state(self):
        with self.connection() as connection:
            return {**self._state(connection), "agent_active": self._agent_active(connection)}

    @staticmethod
    def _agent_active(connection):
        row = connection.execute("SELECT value FROM meta WHERE key='agent_heartbeat'").fetchone()
        if row is None:
            return False
        try:
            age = time.time() - float(row[0])
        except (ValueError, TypeError):
            return False
        return 0 <= age <= 35

    def _heartbeat(self):
        # Presence is operational metadata, never a proposal/feedback mutation.
        # Do not delay an approved export or review writer for housekeeping.
        try:
            with self.connection(timeout=0.1) as connection:
                connection.execute("INSERT OR REPLACE INTO meta(key,value) VALUES ('agent_heartbeat', ?)",
                                   (str(time.time()),))
            return True
        except sqlite3.OperationalError as error:
            if "locked" not in str(error).lower() and "busy" not in str(error).lower():
                raise
            return False

    def wait(self, after_version, timeout=20):
        if type(after_version) is not int or after_version < 0:
            raise ReviewError("after_version must be a nonnegative integer.")
        if type(timeout) not in (int, float) or not math.isfinite(timeout) or not 0 <= timeout <= 30:
            raise ReviewError("timeout must be between 0 and 30 seconds.")
        deadline = time.monotonic() + timeout
        next_heartbeat = 0.0
        while True:
            now = time.monotonic()
            if now >= next_heartbeat:
                next_heartbeat = now + (10 if self._heartbeat() else 1)
            state = self.state()
            changed = state["state_version"] != after_version
            remaining = deadline - time.monotonic()
            if changed or remaining <= 0:
                return {**state, "changed": changed}
            time.sleep(min(0.2, remaining))

    def disconnect(self):
        with self.connection(timeout=1) as connection:
            connection.execute("DELETE FROM meta WHERE key='agent_heartbeat'")
        return self.state()

    def token(self):
        with self.connection() as connection:
            return connection.execute("SELECT value FROM meta WHERE key='csrf_token'").fetchone()[0]

    def round_snapshot(self, number):
        if type(number) is not int or number < 1:
            raise ReviewError("round must be a positive integer.")
        with self.connection() as connection:
            row = connection.execute("SELECT content FROM rounds WHERE round=?", (number,)).fetchone()
        if row is None:
            raise ReviewError("Review round not found.", 404)
        return json.loads(row[0])

    def mutate(self, expected_version, action, actor, operation, invalidate=True):
        if type(expected_version) is not int or expected_version < 1:
            raise ReviewError("expected_version must be a positive integer.")
        with self.connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                state = self._state(connection)
                if state["state_version"] != expected_version:
                    raise ReviewError(
                        f"Review changed: expected version {expected_version}, current version "
                        f"{state['state_version']}. Refresh and review the latest state.", 409)
                payload = operation(state, connection)
                if invalidate:
                    state["approval"] = None
                    state["finalized"] = None
                state["state_version"] += 1
                connection.execute("UPDATE state SET content=? WHERE id=1", (canonical(state),))
                connection.execute("INSERT INTO audit(content) VALUES (?)", (canonical({
                    "action": action, "source": actor, "at": timestamp(),
                    "state_version": state["state_version"], "round": state["round"],
                    "payload": payload,
                }),))
                connection.commit()
                return {**state, "agent_active": self._agent_active(connection)}
            except Exception:
                connection.rollback()
                raise

    def propose(self, graph, summary, expected_version):
        validate_graph(graph)
        summary = text_value(summary, "summary")
        graph = json.loads(canonical(graph))

        def operation(state, connection):
            metadata, reopened = self.apply_proposal(state, graph, summary)
            snapshot = {**metadata, "graph": graph}
            connection.execute("INSERT INTO rounds VALUES (?, ?)", (metadata["round"], canonical(snapshot)))
            return {**metadata, "reopened_annotations": reopened}

        return self.mutate(expected_version, "proposed", "agent", operation)

    def export(self, out, expected_version):
        destination = Path(out).expanduser().absolute()
        # resolve() on an existing symlink could hide an existing output path.
        if destination.exists() or destination.is_symlink():
            raise ReviewError("Output path already exists. Choose a new directory; no files were overwritten.", 409)
        destination = destination.parent.resolve() / destination.name
        if destination == self.directory or self.directory in destination.parents:
            raise ReviewError("Final output must be separate from the review-session directory.")

        def operation(state, connection):
            self.settled(state)
            approval = state["approval"]
            if not approval or approval["round"] != state["round"] or approval["digest"] != state["graph_digest"]:
                raise ReviewError("The exact current proposal has not been approved by the user.", 409)
            if approval.get("review_digest") != review_digest(state):
                raise ReviewError("Feedback changed after approval. A fresh user approval is required.", 409)
            if destination.exists() or destination.is_symlink():
                raise ReviewError("Output path already exists; no files were overwritten.", 409)
            destination.parent.mkdir(parents=True, exist_ok=True)
            # Cooperating exports from different sessions serialize on the destination.
            lock = destination.parent / (".graph-export-" + hashlib.sha256(str(destination).encode()).hexdigest()[:20] + ".lock")
            try:
                descriptor = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
                os.close(descriptor)
            except FileExistsError as error:
                raise ReviewError("Another export holds this destination. Choose another path or inspect the stale lock.", 409) from error
            staging = None
            try:
                if destination.exists() or destination.is_symlink():
                    raise ReviewError("Output path already exists; no files were overwritten.", 409)
                staging = Path(tempfile.mkdtemp(prefix=".graph-staging-", dir=destination.parent))
                graph = state["graph"]
                files = {}

                def write(relative, value):
                    content = (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False) + "\n").encode("utf-8")
                    path = staging / relative
                    path.parent.mkdir(parents=True, exist_ok=True)
                    with path.open("xb") as stream:
                        stream.write(content)
                        stream.flush()
                        os.fsync(stream.fileno())
                    files[relative] = hashlib.sha256(content).hexdigest()

                write("graph.json", graph)
                nodes = []
                for node in graph["nodes"]:
                    relative = "nodes/node-" + node["id"] + ".json"
                    write(relative, node)
                    nodes.append({key: node[key] for key in ("id", "kind", "title", "summary", "domain", "aliases")})
                    nodes[-1]["path"] = relative
                write("index.json", {"schema_version": SCHEMA_VERSION, "project": graph["project"],
                                     "graph_digest": state["graph_digest"], "nodes": nodes})
                for key in ("dependencies", "evidence", "coverage"):
                    write(key + ".json", graph[key])
                events = [json.loads(row[0]) for row in connection.execute("SELECT content FROM audit ORDER BY id")]
                write("review.json", {
                    "session_id": state["session_id"], "round": state["round"],
                    "graph_digest": state["graph_digest"], "approval": approval,
                    "annotations": state["annotations"], "rounds": state["rounds"],
                    "sources": graph["project"]["sources"], "coverage": graph["coverage"],
                    "audit": events,
                })
                manifest = {"schema_version": "behavior-graph-export/1", "graph_schema_version": SCHEMA_VERSION,
                            "graph_digest": state["graph_digest"], "round": state["round"],
                            "approved_at": approval["created_at"], "created_at": timestamp(),
                            "files": dict(files)}
                write("manifest.json", manifest)
                if destination.exists() or destination.is_symlink():
                    raise ReviewError("Output path appeared during export; no files were overwritten.", 409)
                os.rename(staging, destination)
                staging = None
                receipt = {"path": str(destination), "round": state["round"],
                           "graph_digest": state["graph_digest"], "created_at": timestamp(),
                           "manifest_sha256": files["manifest.json"]}
                state["finalized"] = receipt
                return receipt
            finally:
                if staging is not None:
                    shutil.rmtree(staging)
                lock.unlink(missing_ok=True)

        return self.mutate(expected_version, "exported", "agent", operation, invalidate=False)
