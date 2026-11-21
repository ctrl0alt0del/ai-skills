"""Graph and review repositories over versioned JSON documents.

Filesystem, SQLite, and S3 adapters implement the small DocumentStore port.
Review changes reuse ReviewActions, and atomically replace one state+audit
document. Graphs are immutable documents; reviews retain their exact graph
revision, even when the independently configured catalog advances.

Reads never provision storage. publish_graph/initialize_review/import_session
are explicit authoring operations used by the CLI or a trusted integration.
"""

from copy import deepcopy
import hashlib
import re
from typing import Protocol
import uuid

from graph_contract import (ReviewError, canonical, digest, normalize_feedback,
                            text_value, timestamp, validate_graph)
from graph_repositories import review_dto
from review_actions import ReviewActions


CATALOG_KEY = "catalog.json"
CATALOG_SCHEMA = "behavior-graph-catalog/1"
REVIEW_SCHEMA = "behavior-human-review-document/1"
MAX_DOCUMENT_BYTES = 16 * 1024 * 1024
_KEY_PART = re.compile(r"[A-Za-z0-9_-][A-Za-z0-9._-]*\Z")


class DocumentStore(Protocol):
    """Missing reads are 404; create/CAS conflicts are 409.

    The revision token is opaque and must identify the document returned by the
    same read. CAS persists the entire document, never a field-by-field update.
    A failed CAS must not write state, audit, or processing metadata.
    """

    def read(self, key: str) -> tuple[dict, str]: ...
    def create(self, key: str, document: dict) -> None: ...
    def compare_and_swap(self, key: str, document: dict, expected_token: str) -> None: ...


def document_key(key):
    """Validate generated relative object keys before they reach an adapter."""
    if (not isinstance(key, str) or not key or len(key) > 1000
            or any(not _KEY_PART.fullmatch(part) for part in key.split("/"))):
        raise ReviewError("Document key must be a safe relative object path.")
    return key


def opaque_value(value, name):
    # Do not normalize identifiers: spaces, slashes, colons, and Unicode remain
    # part of their identity. Only the generated storage key is constrained.
    text_value(value, name, 2000)
    return value


def _hash(value):
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def graph_key(graph_id, revision):
    return "graphs/" + _hash(opaque_value(graph_id, "graph_id")) + "/" + _hash(opaque_value(revision, "revision")) + ".json"


def review_key(graph_id):
    return "reviews/" + _hash(opaque_value(graph_id, "graph_id")) + ".json"


def document_bytes(document, max_bytes=MAX_DOCUMENT_BYTES):
    if not isinstance(document, dict):
        raise ReviewError("Stored documents must be JSON objects.")
    try:
        data = (canonical(document) + "\n").encode("utf-8")
    except (TypeError, ValueError, UnicodeError, RecursionError) as error:
        raise ReviewError("Document must contain valid finite UTF-8 JSON values.") from error
    if len(data) > max_bytes:
        raise ReviewError("Document exceeds the configured storage size limit.", 413)
    return data


def _catalog(document):
    if (not isinstance(document, dict) or document.get("schema_version") != CATALOG_SCHEMA
            or not isinstance(document.get("graphs"), list)):
        raise ReviewError("Stored graph catalog has an invalid format.", 409)
    identifiers = set()
    for item in document["graphs"]:
        if not isinstance(item, dict) or not {"id", "title", "revision", "digest", "node_count"} <= item.keys():
            raise ReviewError("Stored graph catalog contains an invalid summary.", 409)
        opaque_value(item["id"], "stored graph ID")
        opaque_value(item["revision"], "stored graph revision")
        text_value(item["title"], "stored graph title", 200)
        if item["id"] in identifiers:
            raise ReviewError("Stored graph catalog contains duplicate graph IDs.", 409)
        if (not isinstance(item["digest"], str) or not re.fullmatch(r"[0-9a-f]{64}", item["digest"])
                or type(item["node_count"]) is not int or item["node_count"] < 1):
            raise ReviewError("Stored graph catalog contains invalid graph metadata.", 409)
        identifiers.add(item["id"])
    return document


def _snapshot(snapshot, graph_id, revision):
    if (not isinstance(snapshot, dict) or snapshot.get("id") != graph_id
            or snapshot.get("revision") != revision or "graph" not in snapshot):
        raise ReviewError("Stored graph snapshot does not match its requested identity.", 409)
    text_value(snapshot.get("title"), "graph title", 200)
    validate_graph(snapshot["graph"])
    if digest(snapshot["graph"]) != snapshot.get("digest"):
        raise ReviewError("Stored graph revision does not match its digest.", 409)
    return snapshot


class DocumentGraphRepository:
    def __init__(self, store: DocumentStore):
        self.store = store

    def list_graphs(self):
        catalog, _ = self.store.read(CATALOG_KEY)
        return deepcopy(_catalog(catalog)["graphs"])

    def get_graph(self, graph_id, revision=None):
        opaque_value(graph_id, "graph_id")
        if revision is None:
            summary = next((item for item in self.list_graphs() if item["id"] == graph_id), None)
            if summary is None:
                raise ReviewError("Graph not found.", 404)
            revision = summary["revision"]
        else:
            summary = None
        opaque_value(revision, "revision")
        snapshot, _ = self.store.read(graph_key(graph_id, revision))
        _snapshot(snapshot, graph_id, revision)
        if summary is not None and (snapshot["digest"] != summary["digest"]
                                    or len(snapshot["graph"]["nodes"]) != summary["node_count"]):
            raise ReviewError("Graph catalog does not match its immutable snapshot.", 409)
        return deepcopy(snapshot)

    def initialize_catalog(self):
        """Explicitly provision an empty catalog; never called by a read."""
        self.store.create(CATALOG_KEY, {"schema_version": CATALOG_SCHEMA, "graphs": []})

    def publish_graph(self, graph_id, revision, graph, title=None):
        """Publish an immutable revision and atomically advance its catalog entry.

        The immutable document is written first. A catalog conflict can leave an
        unlisted snapshot, which is safe to retry with the identical arguments.
        It never exposes a catalog entry pointing at a not-yet-written graph.
        Reviews advance only through an explicit proposal operation.
        """
        opaque_value(graph_id, "graph_id")
        opaque_value(revision, "revision")
        validate_graph(graph)
        title = text_value(title if title is not None else graph["project"]["name"], "title", 200)
        snapshot = {"id": graph_id, "title": title, "revision": revision,
                    "digest": digest(graph), "graph": deepcopy(graph)}
        try:
            catalog, token = self.store.read(CATALOG_KEY)
            _catalog(catalog)
        except ReviewError as error:
            if error.status != 404:
                raise
            catalog, token = {"schema_version": CATALOG_SCHEMA, "graphs": []}, None
        try:
            self.store.create(graph_key(graph_id, revision), snapshot)
        except ReviewError as error:
            if error.status != 409:
                raise
            existing, _ = self.store.read(graph_key(graph_id, revision))
            if existing != snapshot:
                raise ReviewError("This graph revision already identifies different immutable content.", 409) from error
        summary = {key: snapshot[key] for key in ("id", "title", "revision", "digest")}
        summary["node_count"] = len(graph["nodes"])
        found = next((index for index, item in enumerate(catalog["graphs"]) if item["id"] == graph_id), None)
        if found is None:
            catalog["graphs"].append(summary)
        elif catalog["graphs"][found] == summary:
            return snapshot
        else:
            catalog["graphs"][found] = summary
        if token is None:
            self.store.create(CATALOG_KEY, catalog)
        else:
            self.store.compare_and_swap(CATALOG_KEY, catalog, token)
        return snapshot


def _normalize_document(document, graph_id):
    if (not isinstance(document, dict) or document.get("schema_version") != REVIEW_SCHEMA
            or not isinstance(document.get("state"), dict) or not isinstance(document.get("audit"), list)):
        raise ReviewError("Stored review document has an invalid format.", 409)
    state = document["state"]
    required = {"graph_id", "session_id", "title", "graph_revision", "state_version", "round", "graph_digest",
                "annotations", "rounds", "approval", "review_requested", "finalized"}
    if not required <= state.keys() or state["graph_id"] != graph_id or "graph" in state:
        raise ReviewError("Stored review does not match its graph or contains embedded graph content.", 409)
    if (type(state["state_version"]) is not int or state["state_version"] < 1
            or type(state["round"]) is not int or state["round"] < 1
            or not isinstance(state["annotations"], list) or not isinstance(state["rounds"], list)
            or type(state["review_requested"]) is not bool):
        raise ReviewError("Stored review state is invalid.", 409)
    opaque_value(state["graph_revision"], "stored graph revision")
    text_value(state["title"], "stored review title", 200)
    current = next((item for item in state["rounds"] if item.get("round") == state["round"]), None)
    if (current is None or current.get("graph_revision") != state["graph_revision"]
            or current.get("digest") != state["graph_digest"]):
        raise ReviewError("Stored review round does not match its pinned graph revision.", 409)
    for note in state["annotations"]:
        if not isinstance(note, dict):
            raise ReviewError("Stored annotation is invalid.", 409)
        normalize_feedback(note)
    return document


class DocumentReviewSession(ReviewActions):
    """Bind shared domain actions to one CAS document and one graph identity."""

    def __init__(self, store, graph_repository, graph_id):
        self.store = store
        self.graph_repository = graph_repository
        self.graph_id = opaque_value(graph_id, "graph_id")

    def _read(self):
        document, token = self.store.read(review_key(self.graph_id))
        # Defensively copy: an in-memory test or user-supplied store may return
        # references. Failed domain operations must not mutate its contents.
        document = _normalize_document(deepcopy(document), self.graph_id)
        state = document["state"]
        snapshot = self.graph_repository.get_graph(self.graph_id, state["graph_revision"])
        _snapshot(snapshot, self.graph_id, state["graph_revision"])
        if snapshot["digest"] != state["graph_digest"]:
            raise ReviewError("The review's pinned graph revision no longer matches its digest.", 409)
        state["graph"] = deepcopy(snapshot["graph"])
        return document, token

    def state(self):
        document, _ = self._read()
        return {**document["state"], "agent_active": False}

    def propose(self, graph, summary, expected_version, revision=None):
        """Publish a new graph and CAS-bind this review to its exact revision.

        A stale expected review version fails before publication. If another
        reviewer writes during graph publication, final review CAS still fails:
        the immutable graph/catalog may have advanced but this review remains
        pinned to its old snapshot. Inspect current feedback before retrying.
        """
        publisher = getattr(self.graph_repository, "publish_graph", None)
        if not callable(publisher):
            raise ReviewError("The selected graph repository does not support graph publication.", 501)
        validate_graph(graph)
        graph = deepcopy(graph)
        summary = text_value(summary, "summary")
        revision = uuid.uuid4().hex if revision is None else opaque_value(revision, "revision")

        def operation(state, connection):
            snapshot = publisher(self.graph_id, revision, graph, state["title"])
            _snapshot(snapshot, self.graph_id, revision)
            if snapshot["digest"] != digest(graph):
                raise ReviewError("Graph publication returned content that differs from the proposed graph.", 409)
            metadata, reopened = self.apply_proposal(state, graph, summary, revision)
            return {**metadata, "reopened_annotations": reopened}

        return self.mutate(expected_version, "proposed", "agent", operation)

    def mutate(self, expected_version, action, actor, operation, invalidate=True):
        if type(expected_version) is not int or expected_version < 1:
            raise ReviewError("expected_version must be a positive integer.")
        document, token = self._read()
        state = document["state"]
        if state["state_version"] != expected_version:
            raise ReviewError(f"Review changed: expected version {expected_version}, current version "
                              f"{state['state_version']}. Refresh and review the latest state.", 409)
        payload = operation(state, None)
        if invalidate:
            state["approval"] = None
            state["finalized"] = None
        state["state_version"] += 1
        document["audit"].append({"action": action, "source": actor, "at": timestamp(),
                                  "state_version": state["state_version"], "round": state["round"],
                                  "graph_revision": state["graph_revision"], "payload": deepcopy(payload)})
        result = {**deepcopy(state), "agent_active": False}
        state.pop("graph", None)
        state.pop("agent_active", None)
        self.store.compare_and_swap(review_key(self.graph_id), document, token)
        return result


class DocumentHumanReviewRepository:
    def __init__(self, store: DocumentStore, graph_repository):
        self.store = store
        self.graph_repository = graph_repository

    def session(self, graph_id):
        """Shared actions for trusted agent/chat workflows, with the same CAS."""
        return DocumentReviewSession(self.store, self.graph_repository, graph_id)

    def get_review(self, graph_id):
        return review_dto(self.session(graph_id).state())

    def list_annotations(self, graph_id, include_deleted=False):
        return [note for note in self.get_review(graph_id)["annotations"]
                if include_deleted or note["status"] != "deleted"]

    def get_annotation(self, graph_id, annotation_id, include_deleted=False):
        for note in self.list_annotations(graph_id, include_deleted):
            if note["id"] == annotation_id:
                return note
        raise ReviewError("Annotation not found.", 404)

    def create_annotation(self, graph_id, annotation, expected_version):
        if not isinstance(annotation, dict) or set(annotation) != {"target_type", "target_id", "kind", "text"}:
            raise ReviewError("An annotation requires exactly target_type, target_id, kind, and text.")
        return review_dto(self.session(graph_id).annotate(annotation["target_type"], annotation["target_id"],
                                                       annotation["kind"], annotation["text"], "ui", None, expected_version))

    def update_annotation(self, graph_id, annotation_id, changes, expected_version):
        return review_dto(self.session(graph_id).edit_annotation(annotation_id, changes, "ui", expected_version))

    def delete_annotation(self, graph_id, annotation_id, expected_version):
        return review_dto(self.session(graph_id).delete_annotation(annotation_id, "ui", expected_version))

    def resolve_annotation(self, graph_id, annotation_id, disposition, text, expected_version):
        return review_dto(self.session(graph_id).resolve(annotation_id, disposition, text, "ui", expected_version))

    def reopen_annotation(self, graph_id, annotation_id, expected_version):
        return review_dto(self.session(graph_id).reopen(annotation_id, expected_version, "ui"))

    def request_review(self, graph_id, expected_version):
        return review_dto(self.session(graph_id).request_review(expected_version))

    def complete_review(self, graph_id, summary, expected_version):
        return review_dto(self.session(graph_id).complete_review(summary, expected_version))

    def approve_review(self, graph_id, text, acknowledge_limits, expected_version):
        return review_dto(self.session(graph_id).approve(text, "ui", None, acknowledge_limits, expected_version))

    def list_pending_feedback(self, graph_id):
        return self.session(graph_id).list_pending_feedback()

    def mark_feedback_processed(self, graph_id, annotation_id, feedback_revision,
                                outcome, summary, processor, expected_version):
        return review_dto(self.session(graph_id).mark_feedback_processed(annotation_id, feedback_revision, outcome,
                                                                        summary, processor, expected_version))

    def initialize_review(self, graph_id, revision=None, title=None, summary="Imported proposal"):
        """Explicitly start a review pinned to one immutable graph snapshot."""
        snapshot = self.graph_repository.get_graph(graph_id, revision)
        _snapshot(snapshot, graph_id, snapshot["revision"])
        title = text_value(title if title is not None else snapshot["title"], "title", 200)
        summary = text_value(summary, "summary")
        metadata = {"round": 1, "graph_revision": snapshot["revision"], "digest": snapshot["digest"],
                    "summary": summary, "created_at": timestamp(), "node_count": len(snapshot["graph"]["nodes"]),
                    "dependency_count": len(snapshot["graph"]["dependencies"])}
        state = {"session_id": graph_id, "graph_id": graph_id, "title": title, "state_version": 1,
                 "round": 1, "graph_revision": snapshot["revision"], "graph_digest": snapshot["digest"],
                 "annotations": [], "rounds": [metadata], "approval": None,
                 "review_requested": False, "finalized": None}
        self.store.create(review_key(graph_id), {"schema_version": REVIEW_SCHEMA, "state": state,
                                               "audit": [{"action": "initialized", "at": timestamp(),
                                                          "state_version": 1, "title": title,
                                                          "graph_revision": snapshot["revision"]}]})
        return review_dto({**state, "agent_active": False})

    def import_session(self, session):
        """Copy one legacy session, preserving IDs, versions, notes, and history.

        All source records are read under one SQLite snapshot. Destination
        reviews must be absent; graph revisions are immutable and may be reused
        only when identical. An interrupted import may be safely retried until
        the final review document is created.
        """
        import json
        from graph_model import Store
        session = session if isinstance(session, Store) else Store(session)
        publisher = getattr(self.graph_repository, "publish_graph", None)
        if not callable(publisher):
            raise ReviewError("The selected graph repository cannot publish imported graph snapshots.", 501)
        with session.connection() as connection:
            connection.execute("BEGIN")
            state = deepcopy(session._state(connection))
            if state["round"] < 1:
                raise ReviewError("Present a graph proposal before importing this session.", 409)
            snapshots = {row[0]: json.loads(row[1]) for row in connection.execute("SELECT round, content FROM rounds ORDER BY round")}
            audit = [json.loads(row[0]) for row in connection.execute("SELECT content FROM audit ORDER BY id")]
        graph_id = state["session_id"]
        try:
            self.store.read(review_key(graph_id))
        except ReviewError as error:
            if error.status != 404:
                raise
        else:
            raise ReviewError("A review for this graph already exists; import will not overwrite it.", 409)
        for metadata in state["rounds"]:
            historical = snapshots.get(metadata["round"])
            if historical is None or digest(historical["graph"]) != metadata["digest"]:
                raise ReviewError("The source session has an inconsistent graph history.", 409)
        if digest(state["graph"]) != state["graph_digest"]:
            raise ReviewError("The source session's current graph does not match its digest.", 409)
        for metadata in state["rounds"]:
            revision = str(metadata["round"])
            publisher(graph_id, revision, snapshots[metadata["round"]]["graph"], state["title"])
            metadata["graph_revision"] = revision
        state.pop("graph", None)
        state.pop("agent_active", None)
        state.update(graph_id=graph_id, graph_revision=str(state["round"]))
        self.store.create(review_key(graph_id), {"schema_version": REVIEW_SCHEMA, "state": state, "audit": audit})
        return review_dto({**state, "agent_active": False})


def import_graph(graph_repository, review_repository, graph_id, revision, graph, title=None,
                 summary="Imported proposal"):
    """Convenience provisioning operation for a new graph and its first review."""
    if not callable(getattr(graph_repository, "publish_graph", None)):
        raise ReviewError("The selected graph repository does not support graph publication.", 501)
    if not callable(getattr(review_repository, "initialize_review", None)):
        raise ReviewError("The selected review repository does not support explicit initialization.", 501)
    graph_repository.publish_graph(graph_id, revision, graph, title)
    return review_repository.initialize_review(graph_id, revision, title, summary)
