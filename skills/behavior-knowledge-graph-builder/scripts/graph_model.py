"""Graph contract and review-session persistence. Python standard library only."""

from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import re
import secrets
import shutil
import sqlite3
import tempfile
import time
import uuid


SCHEMA_VERSION = "behavior-graph/1"
MAX_GRAPH_BYTES = 8 * 1024 * 1024
ID = re.compile(r"[a-z0-9][a-z0-9._-]{0,119}\Z")
RELATIONSHIPS = {
    "requires", "implements", "details", "conflicts", "reads", "writes",
    "emits", "consumes", "reverses", "enforces", "exposes", "configured_by",
}
NODE_KINDS = {"behavior", "state", "event", "rule", "requirement", "interface", "configuration"}
ASPECTS = {
    "actor", "trigger", "precondition", "state_transition", "outcome", "invariant",
    "timing", "failure", "recovery", "assumption", "other",
}
KINDS = {"question", "correction", "missing", "confirmation", "note"}
DISPOSITIONS = {"addressed", "accepted_gap", "rejected"}
TARGET_TYPES = {"graph", "node", "dependency", "claim"}
GRAPH_SCHEMA = {
    "schema_version": {SCHEMA_VERSION},
    "project": {
        "name": str, "scope": [str],
        "sources": [{"id": str, "kind": {"repository", "document", "conversation"},
                     "location": str, "revision": (str, type(None))}],
    },
    "nodes": [{
        "id": str, "kind": NODE_KINDS, "title": str, "summary": str,
        "domain": str, "aliases": [str], "owner_role": (str, type(None)),
        "claims": [{"id": str, "aspect": ASPECTS,
                    "perspective": {"intended", "observed", "proposed"},
                    "basis": {"supported", "inferred", "unknown"},
                    "statement": str, "evidence_ids": [str]}],
    }],
    "dependencies": [{
        "id": str, "from": str, "relationship": RELATIONSHIPS, "to": str,
        "basis": {"supported", "inferred"}, "evidence_ids": [str],
        "rationale": str, "conditions": [str],
    }],
    "evidence": [{
        "id": str,
        "kind": {"requirement", "approved-spec", "documentation", "code", "test",
                 "contract", "observation", "human-feedback"},
        "location": str, "source_id": str, "summary": str,
    }],
    "coverage": {"checked": [str], "blind_spots": [str], "unexpanded": [str], "conflicts": [str]},
}


class ReviewError(Exception):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status


def timestamp():
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def digest(value):
    return hashlib.sha256(canonical(value).encode("utf-8")).hexdigest()


def _unique_object(pairs):
    output = {}
    for key, value in pairs:
        if key in output:
            raise ValueError("Duplicate JSON object key: " + key)
        output[key] = value
    return output


def _reject_constant(value):
    raise ValueError("Invalid JSON constant: " + value)


def parse_json(text):
    try:
        return json.loads(text, object_pairs_hook=_unique_object, parse_constant=_reject_constant)
    except (ValueError, RecursionError) as error:
        raise ReviewError("Invalid JSON: " + str(error)) from error


def read_graph(path):
    try:
        with Path(path).open("rb") as stream:
            data = stream.read(MAX_GRAPH_BYTES + 1)
        if len(data) > MAX_GRAPH_BYTES:
            raise ReviewError("Graph exceeds the 8 MiB limit.")
        graph = parse_json(data.decode("utf-8-sig"))
    except (OSError, UnicodeError) as error:
        raise ReviewError("Cannot read graph: " + str(error)) from error
    validate_graph(graph)
    return graph


def validate_graph(graph):
    errors = []

    def check(value, schema, path):
        if schema is str:
            if not isinstance(value, str) or not value.strip():
                errors.append(path + ": expected a nonempty string")
            elif len(value) > 60000:
                errors.append(path + ": string exceeds 60,000 characters")
            else:
                try:
                    value.encode("utf-8")
                except UnicodeError:
                    errors.append(path + ": invalid Unicode string")
        elif isinstance(schema, tuple):
            if value is not None:
                check(value, str, path)
        elif isinstance(schema, set):
            if not isinstance(value, str) or value not in schema:
                errors.append(path + ": expected one of " + ", ".join(sorted(schema)))
        elif isinstance(schema, list):
            if not isinstance(value, list):
                errors.append(path + ": expected an array")
            else:
                for index, item in enumerate(value):
                    check(item, schema[0], f"{path}[{index}]")
        elif isinstance(schema, dict):
            if not isinstance(value, dict):
                errors.append(path + ": expected an object")
                return
            for key in schema:
                if key not in value:
                    errors.append(path + "." + key + ": missing required field")
                else:
                    check(value[key], schema[key], path + "." + key)
            for key in value.keys() - schema.keys():
                errors.append(path + "." + key + ": unexpected field")

    try:
        check(graph, GRAPH_SCHEMA, "graph")
    except RecursionError as error:
        raise ReviewError("Graph nesting is too deep.") from error
    if errors:
        raise ReviewError("Invalid graph:\n" + "\n".join(errors[:50]))
    if not graph["nodes"]:
        errors.append("A reviewable proposal requires at least one node.")
    if not graph["project"]["scope"]:
        errors.append("project.scope must describe the bounded scope.")
    if not graph["coverage"]["checked"]:
        errors.append("coverage.checked must identify inspected material.")

    collections = {
        "sources": graph["project"]["sources"], "nodes": graph["nodes"],
        "dependencies": graph["dependencies"], "evidence": graph["evidence"],
        "claims": [claim for node in graph["nodes"] for claim in node["claims"]],
    }
    known = {}
    for name, records in collections.items():
        ids = set()
        for record in records:
            identifier = record["id"]
            if not ID.fullmatch(identifier):
                errors.append(f"{name}: invalid ID {identifier!r}")
            if identifier in ids:
                errors.append(f"{name}: duplicate ID {identifier}")
            ids.add(identifier)
        known[name] = ids

    for record in graph["evidence"]:
        if record["source_id"] not in known["sources"]:
            errors.append(record["id"] + ": source_id does not resolve")
    for record in collections["claims"] + graph["dependencies"]:
        refs = record["evidence_ids"]
        if len(refs) != len(set(refs)):
            errors.append(record["id"] + ": duplicate evidence references")
        for ref in refs:
            if ref not in known["evidence"]:
                errors.append(record["id"] + ": unknown evidence " + ref)
        if record["basis"] == "supported" and not refs:
            errors.append(record["id"] + ": supported claims and dependencies require evidence")
    for edge in graph["dependencies"]:
        for endpoint in ("from", "to"):
            if edge[endpoint] not in known["nodes"]:
                errors.append(edge["id"] + ": unknown " + endpoint + " node " + edge[endpoint])
    if errors:
        raise ReviewError("Invalid graph:\n" + "\n".join(errors[:50]))
    return graph


def text_value(value, name, limit=12000):
    if not isinstance(value, str) or not value.strip():
        raise ReviewError(name + " must be a nonempty string.")
    if len(value) > limit:
        raise ReviewError(name + f" exceeds {limit} characters.")
    try:
        value.encode("utf-8")
    except UnicodeError as error:
        raise ReviewError(name + " contains invalid Unicode.") from error
    return value.strip()


def selected(value, choices, name):
    if not isinstance(value, str) or value not in choices:
        raise ReviewError(name + " must be one of " + ", ".join(sorted(choices)))
    return value


def review_digest(state):
    return digest({key: state[key] for key in ("round", "graph_digest", "annotations", "review_requested")})


class Store:
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
        return json.loads(row[0])

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
            number = state["round"] + 1
            graph_hash = digest(graph)
            reopened = []
            if state["graph_digest"] != graph_hash:
                for note in state["annotations"]:
                    if note["status"] == "resolved" and self.target_snapshot(state["graph"], note) != self.target_snapshot(graph, note):
                        reopened.append({"annotation_id": note["id"], "previous_resolution": note["resolution"]})
                        note.update(status="open", resolution=None)
            metadata = {"round": number, "digest": graph_hash, "summary": summary,
                        "created_at": timestamp(), "node_count": len(graph["nodes"]),
                        "dependency_count": len(graph["dependencies"])}
            snapshot = {**metadata, "graph": graph}
            connection.execute("INSERT INTO rounds VALUES (?, ?)", (number, canonical(snapshot)))
            state.update(round=number, graph=graph, graph_digest=graph_hash, review_requested=False)
            state["rounds"].append(metadata)
            return {**metadata, "reopened_annotations": reopened}

        return self.mutate(expected_version, "proposed", "agent", operation)

    @staticmethod
    def target_snapshot(graph, note):
        if graph is None or note["target_type"] == "graph":
            return graph
        if note["target_type"] == "claim":
            records = [claim for node in graph["nodes"] for claim in node["claims"]]
        else:
            records = graph["nodes"] if note["target_type"] == "node" else graph["dependencies"]
        return next((item for item in records if item["id"] == note["target_id"]), None)

    @staticmethod
    def target_exists(state, target_type, target_id):
        selected(target_type, TARGET_TYPES, "target_type")
        if target_type == "graph":
            if target_id is not None:
                raise ReviewError("Graph annotations must use target_id=null.")
            return
        text_value(target_id, "target_id", 120)
        graph = state["graph"]
        if graph is None:
            raise ReviewError("No proposal exists for this target.")
        records = graph["nodes"] if target_type == "node" else graph["dependencies"]
        if target_type == "claim":
            records = [claim for node in graph["nodes"] for claim in node["claims"]]
        if not any(record["id"] == target_id for record in records):
            raise ReviewError("Annotation target does not exist in the current proposal.")

    def annotate(self, target_type, target_id, kind, text, source, source_ref, expected_version):
        selected(kind, KINDS, "kind")
        selected(source, {"ui", "chat", "agent"}, "source")
        text = text_value(text, "text")
        if source_ref is not None:
            source_ref = text_value(source_ref, "source_ref", 2000)

        def operation(state, connection):
            self.target_exists(state, target_type, target_id)
            annotation = {
                "id": "note-" + uuid.uuid4().hex[:16], "target_type": target_type,
                "target_id": target_id, "kind": kind, "text": text, "source": source,
                "source_ref": source_ref, "round": state["round"], "created_at": timestamp(),
                "status": "open", "resolution": None,
            }
            state["annotations"].append(annotation)
            return annotation

        return self.mutate(expected_version, "annotated", source, operation)

    def resolve(self, annotation_id, disposition, text, source, expected_version):
        selected(disposition, DISPOSITIONS, "disposition")
        selected(source, {"ui", "chat", "agent"}, "source")
        text = text_value(text, "resolution text")

        def operation(state, connection):
            note = self._note(state, annotation_id)
            if note["status"] != "open":
                raise ReviewError("Annotation is already resolved. Reopen it before changing its resolution.", 409)
            resolution = {"disposition": disposition, "text": text, "source": source,
                          "round": state["round"], "at": timestamp()}
            note.update(status="resolved", resolution=resolution)
            return {"annotation_id": annotation_id, "resolution": resolution}

        return self.mutate(expected_version, "resolved", source, operation)

    @staticmethod
    def _note(state, identifier):
        for note in state["annotations"]:
            if note["id"] == identifier:
                return note
        raise ReviewError("Annotation not found.", 404)

    def reopen(self, annotation_id, expected_version, source="agent"):
        def operation(state, connection):
            note = self._note(state, annotation_id)
            if note["status"] == "open":
                raise ReviewError("Annotation is already open.", 409)
            previous = note["resolution"]
            note.update(status="open", resolution=None)
            return {"annotation_id": annotation_id, "previous_resolution": previous}

        return self.mutate(expected_version, "reopened", source, operation)

    def request_review(self, expected_version):
        def operation(state, connection):
            state["review_requested"] = True
            return {"message": "Feedback saved. Return to chat and ask the agent to review it."}

        return self.mutate(expected_version, "review_requested", "ui", operation)

    def complete_review(self, summary, expected_version):
        summary = text_value(summary, "summary")

        def operation(state, connection):
            if not state["graph"] or not state["round"]:
                raise ReviewError("Present a proposal before completing review.", 409)
            if any(note["status"] == "open" for note in state["annotations"]):
                raise ReviewError("Disposition all open annotations before completing review.", 409)
            state["review_requested"] = False
            return {"summary": summary}

        return self.mutate(expected_version, "review_completed", "agent", operation)

    @staticmethod
    def settled(state):
        if state["graph"] is None or state["round"] < 1:
            raise ReviewError("There is no reviewable proposal to approve.", 409)
        validate_graph(state["graph"])
        if digest(state["graph"]) != state["graph_digest"]:
            raise ReviewError("The graph snapshot does not match its recorded digest.", 409)
        count = sum(note["status"] == "open" for note in state["annotations"])
        if count:
            raise ReviewError(f"Resolve or explicitly disposition all {count} open annotations first.", 409)
        if state["review_requested"]:
            raise ReviewError("A requested review is still pending. The agent must present the reviewed round first.", 409)

    def approve(self, text, source, source_ref, acknowledge_limits, expected_version):
        selected(source, {"ui", "chat"}, "approval source")
        text = text_value(text, "approval text")
        if acknowledge_limits is not True:
            raise ReviewError("Explicit acknowledgment of scope, uncertainties, and coverage limits is required.")
        if source == "chat":
            source_ref = text_value(source_ref, "source_ref", 2000)

        def operation(state, connection):
            self.settled(state)
            approval = {
                "round": state["round"], "digest": state["graph_digest"], "source": source,
                "source_ref": source_ref, "text": text, "created_at": timestamp(),
                "acknowledge_limits": True, "review_digest": review_digest(state),
                "reviewed_state_version": state["state_version"],
            }
            state["approval"] = approval
            state["finalized"] = None
            return approval

        return self.mutate(expected_version, "approved", source, operation, invalidate=False)

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
