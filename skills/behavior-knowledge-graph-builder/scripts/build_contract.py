"""Portable graph-build packets: graph data and recommendations, never storage authority.

This pure module is bundled identically with the builder and review-app skills.
The app owns review mutations. A builder result cannot acknowledge or approve one.
"""

from copy import deepcopy
from datetime import datetime, timezone
import os
from pathlib import Path
import tempfile
import uuid

from graph_schema import GraphError, canonical, digest, parse_json, validate_graph, text_value


REQUEST_SCHEMA = "behavior-graph-build-request/1"
RESULT_SCHEMA = "behavior-graph-build-result/1"
MAX_PACKET_BYTES = 16 * 1024 * 1024
MODES = {"create", "review", "code_change", "mixed"}
OUTCOMES = {"addressed", "needs_clarification", "no_change", "withdrawn"}
DISPOSITIONS = {"addressed", "accepted_gap", "rejected"}


def fields(value, required, optional=(), label="packet"):
    if (not isinstance(value, dict) or set(required) - value.keys()
            or value.keys() - set(required) - set(optional)):
        raise GraphError(f"Invalid {label} fields.")


def exact_text(value, label, limit=2000):
    text_value(value, label, limit)
    return value


def text_list(value, label):
    if not isinstance(value, list):
        raise GraphError(f"{label} must be an array.")
    for item in value:
        exact_text(item, label, 12000)
    return value


def validate_base(base, graph):
    if base is None:
        if graph is not None:
            raise GraphError("A new target cannot have a base graph.")
        return
    fields(base, ("graph_id", "graph_revision", "graph_digest", "state_version", "round"), label="base reference")
    exact_text(base["graph_id"], "base graph_id")
    exact_text(base["graph_revision"], "base graph_revision")
    if (type(base["state_version"]) is not int or base["state_version"] < 1
            or type(base["round"]) is not int or base["round"] < 0):
        raise GraphError("The base requires a positive review version and nonnegative round.")
    if graph is None:
        if base["graph_digest"] is not None or base["round"] != 0:
            raise GraphError("An empty base requires round zero and no graph digest.")
    else:
        validate_graph(graph)
        if base["round"] < 1 or base["graph_digest"] != digest(graph):
            raise GraphError("Base graph content does not match its exact reference.", 409)


def validate_feedback(notes):
    if not isinstance(notes, list):
        raise GraphError("feedback must be an array of selected annotation snapshots.")
    seen = set()
    for note in notes:
        required = {"id", "feedback_revision", "processed_revision", "target_type", "target_id", "kind", "text", "status"}
        if not isinstance(note, dict) or not required <= note.keys():
            raise GraphError("A feedback snapshot is missing its identity, content, or processing revisions.")
        identifier = exact_text(note["id"], "annotation ID")
        if identifier in seen:
            raise GraphError("Duplicate feedback annotation IDs are not allowed.")
        seen.add(identifier)
        if (type(note["feedback_revision"]) is not int or note["feedback_revision"] < 1
                or type(note["processed_revision"]) is not int or note["processed_revision"] < 0
                or note["processed_revision"] >= note["feedback_revision"]):
            raise GraphError("Selected feedback must identify an exact unprocessed revision.")
        if (not isinstance(note["target_type"], str) or note["target_type"] not in {"graph", "node", "dependency", "claim"}
                or not isinstance(note["status"], str) or note["status"] not in {"open", "resolved", "deleted"}):
            raise GraphError("Invalid feedback target or discussion status.")
        if note["target_type"] == "graph":
            if note["target_id"] is not None:
                raise GraphError("Whole-graph feedback must have target_id=null.")
        else:
            exact_text(note["target_id"], "feedback target ID")
        exact_text(note["kind"], "feedback kind", 200)
        exact_text(note["text"], "feedback text", 12000)


def validate_source_changes(changes):
    if not isinstance(changes, list):
        raise GraphError("source_changes must be an array.")
    for item in changes:
        fields(item, ("source_id", "location", "from_revision", "to_revision", "paths"), ("summary",), "source change")
        exact_text(item["source_id"], "source ID")
        exact_text(item["location"], "source location", 8000)
        for key in ("from_revision", "to_revision"):
            if item[key] is not None:
                exact_text(item[key], key)
        text_list(item["paths"], "changed paths")
        if item["from_revision"] is None and item["to_revision"] is None and not item["paths"]:
            raise GraphError("A source change needs a declared revision or explicit changed paths.")
        if "summary" in item:
            exact_text(item["summary"], "source change summary", 12000)


def validate_request(request):
    fields(request, ("schema_version", "request_id", "created_at", "mode", "scope", "base", "base_graph",
                     "feedback", "source_changes"), ("sources",), label="builder request")
    if request["schema_version"] != REQUEST_SCHEMA or not isinstance(request["mode"], str) or request["mode"] not in MODES:
        raise GraphError("Unsupported builder request schema or mode.")
    exact_text(request["request_id"], "request ID", 200)
    exact_text(request["created_at"], "creation time", 100)
    text_list(request["scope"], "scope")
    text_list(request.get("sources", []), "source locations")
    if not request["scope"]:
        raise GraphError("Declare at least one bounded scope item.")
    validate_base(request["base"], request["base_graph"])
    validate_feedback(request["feedback"])
    validate_source_changes(request["source_changes"])
    if request["mode"] == "create":
        if request["base_graph"] is not None:
            raise GraphError("Create mode requires an empty or absent graph.")
    elif request["base_graph"] is None:
        raise GraphError("Update modes require the existing graph snapshot.")
    if request["mode"] in {"review", "mixed"} and not request["feedback"]:
        raise GraphError("Review and mixed modes require selected feedback.")
    if request["mode"] in {"code_change", "mixed"} and not request["source_changes"]:
        raise GraphError("Code-change and mixed modes require declared source changes.")
    if request["mode"] == "code_change" and request["feedback"]:
        raise GraphError("Use mixed mode to handle feedback with code changes.")
    if request["mode"] == "review" and request["source_changes"]:
        raise GraphError("Use mixed mode to handle code changes with feedback.")
    return request


def make_request(mode, scope, base, base_graph, feedback=None, source_changes=None, sources=None):
    if sources is None:
        sources = list(dict.fromkeys([source["location"] for source in (base_graph or {}).get("project", {}).get("sources", [])]
                                     + [change["location"] for change in (source_changes or [])]))
    request = {"schema_version": REQUEST_SCHEMA, "request_id": "build-" + uuid.uuid4().hex,
               "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
               "mode": mode, "scope": deepcopy(scope), "sources": deepcopy(sources), "base": deepcopy(base), "base_graph": deepcopy(base_graph),
               "feedback": deepcopy(feedback or []), "source_changes": deepcopy(source_changes or [])}
    return validate_request(request)


def _records(graph, collection):
    if graph is None:
        return {}
    if collection == "claims":
        records = [claim for node in graph["nodes"] for claim in node["claims"]]
    elif collection == "sources":
        records = graph["project"]["sources"]
    else:
        records = graph[collection]
    return {record["id"]: record for record in records}


def graph_changes(before, after):
    changes = {}
    for collection in ("nodes", "claims", "dependencies", "evidence", "sources"):
        old, new = _records(before, collection), _records(after, collection)
        changes[collection] = {"added": sorted(new.keys() - old.keys()), "removed": sorted(old.keys() - new.keys()),
                               "updated": sorted(key for key in new.keys() & old.keys() if new[key] != old[key])}
    changes["project_changed"] = before is None or before["project"] != after["project"]
    changes["coverage_changed"] = before is None or before["coverage"] != after["coverage"]
    return changes


def validate_report(report, request):
    fields(report, ("summary", "questions", "feedback"), label="builder report")
    exact_text(report["summary"], "result summary", 12000)
    text_list(report["questions"], "remaining questions")
    if not isinstance(report["feedback"], list):
        raise GraphError("Report feedback must be an array.")
    selected = {note["id"]: note for note in request["feedback"]}
    seen = set()
    for recommendation in report["feedback"]:
        fields(recommendation, ("annotation_id", "feedback_revision", "outcome", "summary", "disposition"), label="feedback recommendation")
        identifier = recommendation["annotation_id"]
        if not isinstance(identifier, str) or identifier not in selected or identifier in seen:
            raise GraphError("Report must account for each selected annotation exactly once.")
        seen.add(identifier)
        note = selected[identifier]
        if type(recommendation["feedback_revision"]) is not int or recommendation["feedback_revision"] != note["feedback_revision"]:
            raise GraphError("Feedback recommendation does not match the inspected revision.", 409)
        if (not isinstance(recommendation["outcome"], str) or recommendation["outcome"] not in OUTCOMES
                or recommendation["disposition"] is not None and (not isinstance(recommendation["disposition"], str)
                                                                  or recommendation["disposition"] not in DISPOSITIONS)):
            raise GraphError("Invalid feedback outcome or recommended disposition.")
        exact_text(recommendation["summary"], "feedback recommendation summary", 12000)
        if recommendation["outcome"] == "needs_clarification":
            if recommendation["disposition"] is not None or not report["questions"]:
                raise GraphError("Clarification must remain open and name a remaining question.")
        if recommendation["outcome"] == "withdrawn" and note["status"] != "deleted":
            raise GraphError("Only deleted feedback can be handled as withdrawn.")
        if note["status"] == "deleted" and recommendation["disposition"] is not None:
            raise GraphError("Deleted feedback cannot receive a discussion disposition.")
    if seen != selected.keys():
        raise GraphError("Report must account for each selected annotation exactly once.")
    return report


def make_result(request, graph, report):
    validate_request(request)
    validate_graph(graph)
    validate_report(report, request)
    return {"schema_version": RESULT_SCHEMA, "request_id": request["request_id"],
            "request_digest": digest(request), "base": deepcopy(request["base"]),
            "graph": deepcopy(graph), "graph_digest": digest(graph),
            "changes": graph_changes(request["base_graph"], graph), **deepcopy(report)}


def validate_result(result, request):
    validate_request(request)
    fields(result, ("schema_version", "request_id", "request_digest", "base", "graph", "graph_digest", "changes",
                    "summary", "questions", "feedback"), label="builder result")
    if (result["schema_version"] != RESULT_SCHEMA or result["request_id"] != request["request_id"]
            or result["request_digest"] != digest(request) or result["base"] != request["base"]):
        raise GraphError("Builder result belongs to another request or base snapshot.", 409)
    expected = make_result(request, result["graph"], {key: result[key] for key in ("summary", "questions", "feedback")})
    if result != expected:
        raise GraphError("Builder result graph digest or derived change summary does not match its content.", 409)
    return result


def read_packet(path):
    try:
        with Path(path).open("rb") as stream:
            raw = stream.read(MAX_PACKET_BYTES + 1)
        if len(raw) > MAX_PACKET_BYTES:
            raise GraphError("Builder packet exceeds the 16 MiB limit.", 413)
        return parse_json(raw.decode("utf-8-sig"))
    except (OSError, UnicodeError) as error:
        raise GraphError("Cannot read the requested UTF-8 JSON packet.", 404) from error


def write_new_packet(path, value):
    """Publish a complete UTF-8 JSON artifact without replacing any destination."""
    raw = (canonical(value) + "\n").encode("utf-8")
    if len(raw) > MAX_PACKET_BYTES:
        raise GraphError("Builder packet exceeds the 16 MiB limit.", 413)
    path = Path(path).expanduser().absolute()
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=".build-packet-", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        try:
            os.link(temporary, path)
        except FileExistsError as error:
            raise GraphError("Output already exists; choose a new artifact path.", 409) from error
    finally:
        temporary.unlink(missing_ok=True)
    return path
