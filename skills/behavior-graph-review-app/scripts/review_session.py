#!/usr/bin/env python3
"""Prepare/apply graph-builder handoffs and resume one durable review workflow.

No model invocation or background worker runs here. Builder inputs are immutable,
storage locators stay in an app-only checkpoint, and feedback recommendations
remain coordinator work after the graph proposal has been safely persisted.
"""

import argparse
from copy import deepcopy
import json
from pathlib import Path
import sys

sys.dont_write_bytecode = True

from graph_schema import GraphError, digest, validate_graph
from build_contract import (MODES, fields, make_request, read_packet, validate_request,
                            validate_result, write_new_packet)
from graph_connection import read_connection, session_connection, config_connection
from graph_model import Store
from graph_repositories import check_feedback_repository
from filesystem_repository import FilesystemDocumentStore


CHECKPOINT_SCHEMA = "behavior-graph-review-session/1"
PHASES = {"prepared", "applying", "published", "needs-reconciliation"}


def _connection(locator):
    fields(locator, ("kind", "path"), label="app session locator")
    if not isinstance(locator["path"], str) or not locator["path"]:
        raise GraphError("The session locator path is missing.")
    kind = locator["kind"]
    if kind == "session":
        return session_connection(locator["path"])
    if kind == "connection":
        return read_connection(locator["path"])
    if kind == "config":
        return config_connection(locator["path"])
    raise GraphError("Unsupported app session locator kind.")


def _base(review):
    return {key: review[key] for key in ("graph_id", "graph_revision", "graph_digest", "state_version", "round")}


def current_state(connection, graph_id, *, allow_absent=False, allow_partial=False):
    """Read the exact pinned graph plus an inbox from the same review version."""
    repositories = connection.repositories
    try:
        review = repositories.reviews.get_review(graph_id)
    except GraphError as error:
        if error.status != 404 or not allow_absent:
            raise
        try:
            orphan = repositories.graphs.get_graph(graph_id)
        except GraphError as missing:
            if missing.status != 404:
                raise
            return {"exists": False, "review": None, "graph": None, "pending": None}
        if allow_partial:
            return {"exists": False, "review": None, "graph": orphan, "pending": None, "partial_publication": True}
        raise GraphError("This target already has graph content but no review. Initialize or reconcile that review before preparing a create request.", 409)
    if (review.get("graph_id") != graph_id or not isinstance(review.get("graph_revision"), str)
            or type(review.get("state_version")) is not int or type(review.get("round")) is not int):
        raise GraphError("The configured repository returned an inconsistent review identity.", 409)
    snapshot = repositories.graphs.get_graph(graph_id, review["graph_revision"])
    graph = snapshot.get("graph")
    if graph is not None:
        validate_graph(graph)
    if (snapshot.get("id") != graph_id or snapshot.get("revision") != review["graph_revision"]
            or snapshot.get("digest") != review.get("graph_digest")
            or (digest(graph) if graph is not None else None) != review.get("graph_digest")):
        raise GraphError("Review and immutable graph snapshot do not match.", 409)
    pending = check_feedback_repository(repositories.reviews).list_pending_feedback(graph_id)
    if (pending.get("schema_version") != "behavior-review-feedback/1"
            or any(pending.get(key) != review.get(key) for key in ("graph_id", "graph_revision", "graph_digest", "state_version"))
            or not isinstance(pending.get("annotations"), list)):
        raise GraphError("Feedback changed while preparing the snapshot. Read the current review again.", 409)
    notes = {note["id"]: note for note in review["annotations"]}
    for note in pending["annotations"]:
        if not isinstance(note, dict) or notes.get(note.get("id")) != note:
            raise GraphError("Pending feedback does not match the current review snapshot.", 409)
    return {"exists": True, "review": review, "graph": snapshot, "pending": pending}


def _require_new_target(connection):
    if (not callable(getattr(connection.repositories.graphs, "publish_graph", None))
            or not callable(getattr(connection.repositories.reviews, "initialize_review", None))):
        raise GraphError("Fresh configured targets require graph publication and review initialization capabilities. Use the existing local-session initializer or a document repository; no parallel session was created.", 501)


def prepare(*, locator, work, mode, scope, graph_id=None, source_changes=None, feedback_ids=None, sources=None):
    connection = _connection(locator)
    graph_id = connection.select_graph(graph_id)
    current = current_state(connection, graph_id, allow_absent=mode == "create")
    if not current["exists"]:
        _require_new_target(connection)
        base, graph, pending = None, None, []
    else:
        base, graph = _base(current["review"]), current["graph"]["graph"]
        pending = current["pending"]["annotations"]
    if mode == "code_change":
        if feedback_ids:
            raise GraphError("Use mixed mode to select human feedback alongside code changes.")
        selected = []
    elif feedback_ids is None:
        selected = pending
    else:
        if len(feedback_ids) != len(set(feedback_ids)):
            raise GraphError("Do not select the same annotation twice.")
        by_id = {note["id"]: note for note in pending}
        if any(identifier not in by_id for identifier in feedback_ids):
            raise GraphError("A selected feedback ID is not currently pending for this graph.", 409)
        selected = [by_id[identifier] for identifier in feedback_ids]
    request = make_request(mode, scope, base, graph, selected, source_changes, sources)
    work = Path(work).expanduser().absolute()
    try:
        work.mkdir(parents=True, exist_ok=False, mode=0o700)
    except FileExistsError as error:
        raise GraphError("Handoff work directory already exists. Resume it with status or choose a new directory.", 409) from error
    write_new_packet(work / "request.json", request)
    checkpoint = {"schema_version": CHECKPOINT_SCHEMA, "request_id": request["request_id"],
                  "request_digest": digest(request), "locator": deepcopy(locator), "graph_id": graph_id,
                  "phase": "prepared", "result_digest": None, "publication": None, "failure": None}
    FilesystemDocumentStore(work).create("checkpoint.json", checkpoint)
    return {"work": str(work), "request": str(work / "request.json"), "request_id": request["request_id"],
            "phase": "prepared", "graph_id": graph_id, "selected_feedback": len(selected),
            "next_action": "Invoke behavior-knowledge-graph-builder with request.json; save its result, then apply-result."}


def load_work(work):
    work = Path(work).expanduser().resolve()
    store = FilesystemDocumentStore(work)
    checkpoint, token = store.read("checkpoint.json")
    fields(checkpoint, ("schema_version", "request_id", "request_digest", "locator", "graph_id", "phase",
                        "result_digest", "publication", "failure"), label="app session checkpoint")
    if checkpoint["schema_version"] != CHECKPOINT_SCHEMA or checkpoint["phase"] not in PHASES:
        raise GraphError("Unsupported app checkpoint schema or phase.")
    request = validate_request(read_packet(work / "request.json"))
    if checkpoint["request_id"] != request["request_id"] or checkpoint["request_digest"] != digest(request):
        raise GraphError("The immutable builder request changed after preparation.", 409)
    if request["base"] is not None and checkpoint["graph_id"] != request["base"]["graph_id"]:
        raise GraphError("The checkpoint selects a different graph from its request.", 409)
    return work, store, checkpoint, token, request


def _same_base(request, current):
    if request["base"] is None:
        return not current["exists"] and current["graph"] is None
    if not current["exists"] or _base(current["review"]) != request["base"]:
        return False
    notes = {note["id"]: note for note in current["pending"]["annotations"]}
    return all(notes.get(note["id"]) == note for note in request["feedback"])


def _result_copy(work, result):
    target = work / "result.json"
    if target.exists() or target.is_symlink():
        if target.is_symlink() or read_packet(target) != result:
            raise GraphError("This work directory already has a different result. Prepare a new request.", 409)
    else:
        write_new_packet(target, result)


def status(work):
    work, _, checkpoint, _, request = load_work(work)
    connection = _connection(checkpoint["locator"])
    current = current_state(connection, checkpoint["graph_id"], allow_absent=True, allow_partial=True)
    phase = checkpoint["phase"]
    if phase in {"applying", "needs-reconciliation"}:
        next_action = "Inspect current graph/review and any partial publication before preparing a new request. Do not repeat this apply blindly."
    elif phase == "published":
        next_action = "Inspect result.json and fresh feedback. Apply justified recommendations with existing review commands and exact current revisions; then present the review."
    else:
        next_action = ("Invoke the builder or apply its matching result." if _same_base(request, current)
                       else "The review changed since preparation. Reconcile it and prepare a fresh builder request.")
    output = {"work": str(work), "phase": phase, "request_id": request["request_id"],
              "graph_id": checkpoint["graph_id"], "publication": checkpoint["publication"],
              "base_current": _same_base(request, current), "current": current,
              "reconciliation_required": phase in {"applying", "needs-reconciliation"}, "next_action": next_action}
    if phase in {"applying", "needs-reconciliation"} and callable(getattr(connection.repositories.graphs, "publish_graph", None)):
        # Document proposals use this deterministic immutable revision. It may
        # exist even if a failed catalog update makes latest/review reads miss it.
        try:
            output["publication_candidate"] = connection.repositories.graphs.get_graph(checkpoint["graph_id"], request["request_id"])
        except GraphError as error:
            if error.status != 404:
                raise
            output["publication_candidate"] = None
    result_path = work / "result.json"
    if result_path.is_file():
        result = validate_result(read_packet(result_path), request)
        if checkpoint["result_digest"] is not None and digest(result) != checkpoint["result_digest"]:
            raise GraphError("The saved builder result changed after application started.", 409)
        output["feedback_recommendations"] = result["feedback"]
        output["questions"] = result["questions"]
    return output


def apply_result(work, result_path):
    work, store, checkpoint, token, request = load_work(work)
    result = validate_result(read_packet(result_path), request)
    result_hash = digest(result)
    if checkpoint["phase"] == "published":
        if checkpoint["result_digest"] != result_hash:
            raise GraphError("A different builder result has already been applied for this request.", 409)
        response = status(work)
        response["reused_publication"] = True
        return response
    if checkpoint["phase"] != "prepared":
        raise GraphError("A previous apply may have persisted all or part of this proposal. Run status, inspect the current review, and reconcile before preparing a new request; this apply was not repeated.", 409)
    connection = _connection(checkpoint["locator"])
    current = current_state(connection, checkpoint["graph_id"], allow_absent=request["base"] is None)
    if not _same_base(request, current):
        raise GraphError("The review or selected feedback changed since this request. Reconcile it and prepare a fresh builder request.", 409)
    changed = request["base_graph"] is None or result["graph_digest"] != digest(request["base_graph"])
    session = None
    if request["base"] is None:
        _require_new_target(connection)
    elif changed:
        getter = getattr(connection.repositories.reviews, "session", None)
        if not callable(getter):
            raise GraphError("This repository cannot apply trusted builder proposals. Use its existing authoring integration; the result remains a draft.", 501)
        session = getter(checkpoint["graph_id"])
        if not callable(getattr(session, "propose", None)):
            raise GraphError("This repository does not support graph proposal authoring.", 501)
    _result_copy(work, result)
    applying = {**checkpoint, "phase": "applying", "result_digest": result_hash}
    store.compare_and_swap("checkpoint.json", applying, token)
    _, applying_token = store.read("checkpoint.json")
    try:
        if request["base"] is None:
            revision = request["request_id"]
            connection.repositories.graphs.publish_graph(checkpoint["graph_id"], revision, result["graph"])
            review = connection.repositories.reviews.initialize_review(checkpoint["graph_id"], revision, summary=result["summary"])
        elif changed:
            if isinstance(session, Store):
                state = session.propose(result["graph"], result["summary"], request["base"]["state_version"])
                review = {**state, "graph_id": state["session_id"], "graph_revision": str(state["round"])}
            else:
                review = session.propose(result["graph"], result["summary"], request["base"]["state_version"], request["request_id"])
        else:
            refreshed = current_state(connection, checkpoint["graph_id"])
            if not _same_base(request, refreshed):
                raise GraphError("The review changed while applying an unchanged result. Reconcile before using its recommendations.", 409)
            review = refreshed["review"]
        if review.get("graph_digest") != result["graph_digest"]:
            raise GraphError("Publication did not return the proposed graph digest; inspect the configured repository.", 409)
        publication = {**_base(review), "changed": changed}
        finished = {**applying, "phase": "published", "publication": publication}
        store.compare_and_swap("checkpoint.json", finished, applying_token)
    except Exception:
        # A failed operation may already have published an immutable document or
        # even committed a review before a transport failure. Never auto-retry it.
        try:
            store.compare_and_swap("checkpoint.json", {**applying, "phase": "needs-reconciliation",
                "failure": "Apply did not finish with a verified checkpoint. Inspect authoritative storage before continuing."}, applying_token)
        except Exception:
            pass  # The durable applying phase itself blocks automatic re-apply.
        raise
    response = status(work)
    response["reused_publication"] = False
    return response


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    command = commands.add_parser("prepare")
    locators = command.add_mutually_exclusive_group(required=True)
    for kind in ("session", "connection", "config"):
        locators.add_argument("--" + kind, type=Path)
    command.add_argument("--graph-id")
    command.add_argument("--mode", required=True, choices=sorted(MODES))
    command.add_argument("--scope", action="append", required=True)
    command.add_argument("--source", action="append", help="Project/source location; repeat for multiple sources")
    command.add_argument("--changes", type=Path, help="JSON array of declared source changes")
    command.add_argument("--feedback-id", action="append", help="Select a pending annotation; defaults to all pending in review/mixed/create modes")
    command.add_argument("--work", required=True, type=Path)
    command = commands.add_parser("apply-result")
    command.add_argument("--work", required=True, type=Path)
    command.add_argument("--result", required=True, type=Path)
    command = commands.add_parser("status", aliases=["check-resume"])
    command.add_argument("--work", required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        if args.command == "prepare":
            kind = next(kind for kind in ("session", "connection", "config") if getattr(args, kind) is not None)
            locator = {"kind": kind, "path": str(getattr(args, kind).expanduser().resolve())}
            output = prepare(locator=locator, work=args.work, mode=args.mode, scope=args.scope,
                             graph_id=args.graph_id, source_changes=read_packet(args.changes) if args.changes else [],
                             feedback_ids=args.feedback_id, sources=args.source)
        elif args.command == "apply-result":
            output = apply_result(args.work, args.result)
        else:
            output = status(args.work)
        print(json.dumps(output, ensure_ascii=False, indent=2, allow_nan=False))
        return 0
    except GraphError as error:
        print(json.dumps({"error": str(error)}), file=sys.stderr)
        return 1
    except Exception:
        print(json.dumps({"error": "Review handoff failed. Inspect the saved checkpoint and authoritative repository before retrying; no completion was implied."}), file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
