"""Review-app contract and feedback primitives over the shared graph schema.

Keep graph validation independent of persistence. Re-export graph helpers and
ReviewError for the existing app runtime and external repository integrations.
"""

from graph_schema import (ASPECTS, GRAPH_SCHEMA, ID, MAX_GRAPH_BYTES, NODE_KINDS,
                          RELATIONSHIPS, SCHEMA_VERSION, GraphError, canonical,
                          digest, parse_json, read_graph, selected, text_value,
                          timestamp, validate_graph)

ReviewError = GraphError


KINDS = {"question", "correction", "missing", "confirmation", "note"}
DISPOSITIONS = {"addressed", "accepted_gap", "rejected"}
PROCESSING_OUTCOMES = {"addressed", "needs_clarification", "no_change", "withdrawn"}
PROCESSING_FIELDS = {"feedback_revision", "processed_revision", "processing_status", "processing_receipt"}
TARGET_TYPES = {"graph", "node", "dependency", "claim"}


def review_digest(state):
    # Processing is an operational receipt, not a change to human feedback.
    # Projecting it away also preserves approval digests from older sessions.
    semantic = {key: state[key] for key in ("round", "graph_digest", "review_requested")}
    semantic["annotations"] = [
        {key: value for key, value in note.items() if key not in PROCESSING_FIELDS}
        for note in state["annotations"]
    ]
    return digest(semantic)


def normalize_feedback(note, legacy_pending=False):
    """Expose legacy notes without a write; derive status from monotonic revisions.

    A new agent-authored note has no human feedback revision yet. Legacy agent
    notes with a visible human edit, deletion, or resolution are conservatively
    pending. The local adapter supplies legacy_pending when its audit contains
    a reviewer interaction or reopening that older notes did not retain.
    """
    if "feedback_revision" not in note:
        human_touch = (note.get("source") != "agent"
                       or note.get("updated_by") in {"ui", "chat"}
                       or note.get("deleted_by") in {"ui", "chat"}
                       or (note.get("resolution") or {}).get("source") in {"ui", "chat"})
        note["feedback_revision"] = 1 if human_touch or legacy_pending else 0
    note.setdefault("processed_revision", 0)
    note.setdefault("processing_receipt", None)
    revision, processed = note["feedback_revision"], note["processed_revision"]
    if (type(revision) is not int or type(processed) is not int
            or not 0 <= processed <= revision):
        raise ReviewError("Stored feedback revisions are invalid.", 409)
    note["processing_status"] = "unprocessed" if processed < revision else "processed"
    return note


def advance_feedback(note):
    normalize_feedback(note)
    note["feedback_revision"] += 1
    note["processing_status"] = "unprocessed"

