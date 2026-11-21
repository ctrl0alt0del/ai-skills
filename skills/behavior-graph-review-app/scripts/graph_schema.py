"""Pure behavior-graph schema, strict validation, and canonical JSON helpers.

This module has no app, repository, review-state, or server dependencies. Ship
identical copies with graph-building and review-app skills; parity is tested.
"""

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re


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


class GraphError(Exception):
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
        raise GraphError("Invalid JSON: " + str(error)) from error


def read_graph(path):
    try:
        with Path(path).open("rb") as stream:
            data = stream.read(MAX_GRAPH_BYTES + 1)
        if len(data) > MAX_GRAPH_BYTES:
            raise GraphError("Graph exceeds the 8 MiB limit.")
        graph = parse_json(data.decode("utf-8-sig"))
    except (OSError, UnicodeError) as error:
        raise GraphError("Cannot read graph: " + str(error)) from error
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
        raise GraphError("Graph nesting is too deep.") from error
    if errors:
        raise GraphError("Invalid graph:\n" + "\n".join(errors[:50]))
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
        raise GraphError("Invalid graph:\n" + "\n".join(errors[:50]))
    return graph


def text_value(value, name, limit=12000):
    if not isinstance(value, str) or not value.strip():
        raise GraphError(name + " must be a nonempty string.")
    if len(value) > limit:
        raise GraphError(name + f" exceeds {limit} characters.")
    try:
        value.encode("utf-8")
    except UnicodeError as error:
        raise GraphError(name + " contains invalid Unicode.") from error
    return value.strip()


def selected(value, choices, name):
    if not isinstance(value, str) or value not in choices:
        raise GraphError(name + " must be one of " + ", ".join(sorted(choices)))
    return value
