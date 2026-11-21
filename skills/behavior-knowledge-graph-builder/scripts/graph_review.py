#!/usr/bin/env python3
"""Local, persistent behavior-graph review. Python 3.10+; no third-party packages."""

import argparse
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import ipaddress
import json
import math
from pathlib import Path
import secrets
import socket
import sqlite3
import sys
import time
from urllib.parse import parse_qs, urlsplit, unquote

sys.dont_write_bytecode = True

from graph_model import KINDS, DISPOSITIONS, TARGET_TYPES, PROCESSING_OUTCOMES, Store, ReviewError, parse_json, read_graph
from graph_repositories import check_repositories, check_feedback_repository, local_repositories, load_repositories
from graph_connection import read_connection, session_connection, write_connection


MAX_REQUEST_BYTES = 128 * 1024
DIST = Path(__file__).resolve().parent.parent / "assets" / "review-app" / "dist"


def fields(body, required, optional=()):
    if not isinstance(body, dict):
        raise ReviewError("Request body must be a JSON object.")
    missing = set(required) - body.keys()
    extra = body.keys() - set(required) - set(optional)
    if missing:
        raise ReviewError("Missing fields: " + ", ".join(sorted(missing)))
    if extra:
        raise ReviewError("Unexpected fields: " + ", ".join(sorted(extra)))
    return body


def trusted_origin(value):
    parsed = urlsplit(value)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc or parsed.username or parsed.password:
        raise ReviewError("allowed-origin must be an exact http(s) origin without credentials.")
    if parsed.path not in {"", "/"} or parsed.query or parsed.fragment:
        raise ReviewError("allowed-origin must not contain a path, query, or fragment.")
    try:
        parsed.port
    except ValueError as error:
        raise ReviewError("Invalid allowed-origin port.") from error
    return parsed.scheme.lower() + "://" + parsed.netloc.lower()


def load_dist(directory):
    """Read only manifest-listed, hash-verified built assets; never build at runtime."""
    directory = Path(directory).resolve()
    try:
        manifest = parse_json((directory / "manifest.json").read_text(encoding="utf-8"))
    except OSError as error:
        raise ReviewError("Packaged review app dist is missing. Install a complete release; runtime does not build the app.") from error
    if (not isinstance(manifest, dict) or manifest.get("schema_version") != "behavior-review-app-dist/1"
            or manifest.get("entry") != "index.html" or not isinstance(manifest.get("files"), dict)
            or "index.html" not in manifest["files"]):
        raise ReviewError("Packaged review app manifest is invalid.")
    assets = {}
    mimes = {".html": "text/html; charset=utf-8", ".js": "text/javascript; charset=utf-8",
             ".mjs": "text/javascript; charset=utf-8", ".css": "text/css; charset=utf-8",
             ".json": "application/json; charset=utf-8", ".svg": "image/svg+xml"}
    for relative, expected in manifest["files"].items():
        if (not isinstance(relative, str) or not relative or "\\" in relative
                or relative.startswith("/") or any(part in {"", ".", ".."} for part in relative.split("/"))
                or not isinstance(expected, str) or len(expected) != 64):
            raise ReviewError("Packaged review app manifest contains an unsafe asset path or digest.")
        path = directory / relative
        resolved = path.resolve()
        if directory not in resolved.parents or path.is_symlink():
            raise ReviewError("Packaged review app assets must remain inside dist.")
        try:
            content = path.read_bytes()
        except OSError as error:
            raise ReviewError("A packaged review app asset is missing.") from error
        if hashlib.sha256(content).hexdigest() != expected:
            raise ReviewError("A packaged review app asset does not match its build manifest.")
        assets["/" + relative] = (content, mimes.get(path.suffix, "application/octet-stream"))
    assets["/"] = assets["/index.html"]
    return assets


def make_server(repositories, host="127.0.0.1", port=0, allowed_origin=None, dist=None):
    """Serve the prebuilt app over injected repositories, or a legacy Store."""
    legacy_store = repositories if isinstance(repositories, Store) else None
    legacy_id = legacy_store.state()["session_id"] if legacy_store is not None else None
    repositories = local_repositories(session=legacy_store) if legacy_store is not None else check_repositories(repositories)
    graphs, reviews = repositories.graphs, repositories.reviews
    if host == "localhost":
        host = "127.0.0.1"
    try:
        address = ipaddress.ip_address(host)
    except ValueError as error:
        raise ReviewError("Use a numeric bind address or localhost.") from error
    if not address.is_loopback and allowed_origin is None:
        raise ReviewError("Non-loopback binding requires an explicitly trusted --allowed-origin preview proxy.")
    if type(port) is not int or not 0 <= port <= 65535:
        raise ReviewError("port must be between 0 and 65535.")
    allowed_origin = trusted_origin(allowed_origin) if allowed_origin else None
    graphs.list_graphs()  # Fail before binding if configured storage is unavailable.
    token = legacy_store.token() if legacy_store is not None else secrets.token_urlsafe(32)
    assets = load_dist(DIST if dist is None else dist)
    origins = set()
    hosts = set()

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"
        server_version = "BehaviorReview/2"
        sys_version = ""

        def log_message(self, format, *args):
            pass  # Do not log project text, annotations, or credentials.

        def setup(self):
            super().setup()
            self.connection.settimeout(15)

        def reply(self, status, body, mime="application/json; charset=utf-8"):
            if not isinstance(body, bytes):
                body = (json.dumps(body, ensure_ascii=False, allow_nan=False) + "\n").encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", mime)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; object-src 'none'; base-uri 'none'")
            self.end_headers()
            if self.command != "HEAD":
                try:
                    self.wfile.write(body)
                except (BrokenPipeError, ConnectionResetError):
                    pass

        def check_origin(self, mutation=False):
            host_values = self.headers.get_all("Host", [])
            if len(host_values) != 1 or host_values[0].lower() not in hosts:
                raise ReviewError("Untrusted Host header.", 403)
            origin_values = self.headers.get_all("Origin", [])
            if len(origin_values) > 1:
                raise ReviewError("Ambiguous Origin header.", 403)
            origin = origin_values[0].lower() if origin_values else None
            if origin is not None and origin not in origins:
                raise ReviewError("Untrusted request origin.", 403)
            if self.headers.get("Sec-Fetch-Site") == "cross-site" and origin not in origins:
                raise ReviewError("Cross-site access is not permitted.", 403)
            if mutation:
                if origin not in origins:
                    raise ReviewError("An exact trusted Origin header is required for browser mutations.", 403)
                supplied = self.headers.get_all("X-Review-Token", [])
                if len(supplied) != 1 or not secrets.compare_digest(supplied[0], token):
                    raise ReviewError("Missing or invalid review token. Reload the viewer.", 403)

        def parsed_route(self):
            parsed = urlsplit(self.path)
            # Decode components individually so an opaque graph ID may itself contain a slash.
            parts = [unquote(part, errors="strict") for part in parsed.path.split("/")[1:]]
            query = parse_qs(parsed.query, keep_blank_values=True)
            return parsed, parts, query

        def public_review(self, result):
            return {**result, "csrf_token": token}

        def legacy_state(self, result=None):
            if legacy_id is None:
                raise ReviewError("Use a graph-scoped API route for this repository configuration.", 404)
            result = result if result is not None else reviews.get_review(legacy_id)
            snapshot = graphs.get_graph(legacy_id, result["graph_revision"])
            return {**result, "graph": snapshot["graph"], "csrf_token": token}

        def route_get(self):
            self.check_origin()
            parsed, parts, query = self.parsed_route()
            if parsed.path in assets:
                self.reply(200, *assets[parsed.path])
            elif parsed.path == "/api/bootstrap":
                self.reply(200, {"csrf_token": token})
            elif parsed.path == "/api/health":
                body = {"ok": True, "graph_count": len(graphs.list_graphs())}
                if legacy_id is not None:
                    body["session_id"] = legacy_id
                self.reply(200, body)
            elif parsed.path == "/api/graphs":
                if query:
                    raise ReviewError("Graph catalog does not accept query parameters.")
                self.reply(200, {"graphs": graphs.list_graphs()})
            elif len(parts) >= 3 and parts[:2] == ["api", "graphs"]:
                graph_id = parts[2]
                if len(parts) == 3:
                    if set(query) - {"revision"} or ("revision" in query and len(query["revision"]) != 1):
                        raise ReviewError("Provide at most one graph revision.")
                    self.reply(200, graphs.get_graph(graph_id, query.get("revision", [None])[0]))
                elif len(parts) == 4 and parts[3] == "review":
                    if query:
                        raise ReviewError("Review reads do not accept query parameters.")
                    self.reply(200, self.public_review(reviews.get_review(graph_id)))
                elif len(parts) in {4, 5} and parts[3] == "annotations":
                    if set(query) - {"include_deleted"} or query.get("include_deleted", ["false"]) not in (["true"], ["false"]):
                        raise ReviewError("include_deleted must be one true or false value.")
                    deleted = query.get("include_deleted") == ["true"]
                    if len(parts) == 4:
                        self.reply(200, {"annotations": reviews.list_annotations(graph_id, deleted)})
                    else:
                        self.reply(200, reviews.get_annotation(graph_id, parts[4], deleted))
                else:
                    raise ReviewError("Route not found.", 404)
            elif parsed.path == "/api/state":
                self.reply(200, self.legacy_state())
            elif parsed.path == "/api/round":
                if legacy_id is None:
                    raise ReviewError("Use a graph-scoped revision route.", 404)
                if set(query) != {"round"} or len(query["round"]) != 1:
                    raise ReviewError("Provide one round number.")
                snapshot = graphs.get_graph(legacy_id, query["round"][0])
                state = reviews.get_review(legacy_id)
                metadata = next((item for item in state["rounds"] if str(item["round"]) == snapshot["revision"]), None)
                if metadata is None:
                    raise ReviewError("Review round not found.", 404)
                self.reply(200, {**metadata, "graph": snapshot["graph"]})
            elif parsed.path == "/favicon.ico":
                self.reply(204, b"", "image/x-icon")
            else:
                raise ReviewError("Route not found.", 404)

        def read_body(self):
            if self.headers.get("Transfer-Encoding"):
                raise ReviewError("Transfer-Encoding is not accepted.")
            if self.headers.get("Content-Type", "").split(";")[0].strip().lower() != "application/json":
                raise ReviewError("Content-Type must be application/json.", 415)
            lengths = self.headers.get_all("Content-Length", [])
            if len(lengths) != 1:
                raise ReviewError("Provide one Content-Length header.", 411)
            try:
                length = int(lengths[0])
            except ValueError as error:
                raise ReviewError("Invalid Content-Length.") from error
            if length < 0 or length > MAX_REQUEST_BYTES:
                raise ReviewError("Request exceeds the 128 KiB limit.", 413)
            raw = self.rfile.read(length)
            if len(raw) != length:
                raise ReviewError("Incomplete request body.")
            try:
                return parse_json(raw.decode("utf-8"))
            except UnicodeError as error:
                raise ReviewError("Request must be UTF-8 JSON.") from error

        def route_mutation(self):
            self.check_origin(mutation=True)
            parsed, parts, query = self.parsed_route()
            if parsed.query:
                raise ReviewError("Mutation routes do not accept query parameters.")
            body = self.read_body()
            legacy = False
            if len(parts) >= 4 and parts[:2] == ["api", "graphs"]:
                graph_id, route = parts[2], parts[3:]
            elif legacy_id is not None and len(parts) == 2 and parts[0] == "api":
                graph_id, route, legacy = legacy_id, parts[1:], True
            else:
                raise ReviewError("Route not found.", 404)
            if route == ["annotations"] and self.command == "POST":
                fields(body, ("expected_version", "target_type", "target_id", "kind", "text"))
                result = reviews.create_annotation(graph_id, {k: v for k, v in body.items() if k != "expected_version"}, body["expected_version"])
            elif len(route) == 2 and route[0] == "annotations" and self.command == "PATCH":
                fields(body, ("expected_version",), ("text", "kind"))
                if not (body.keys() & {"text", "kind"}):
                    raise ReviewError("Provide text and/or kind for an annotation edit.")
                result = reviews.update_annotation(graph_id, route[1], {k: v for k, v in body.items() if k != "expected_version"}, body["expected_version"])
            elif len(route) == 2 and route[0] == "annotations" and self.command == "DELETE":
                fields(body, ("expected_version",))
                result = reviews.delete_annotation(graph_id, route[1], body["expected_version"])
            elif self.command != "POST":
                raise ReviewError("Method is not supported for this route.", 405)
            elif route == ["resolve"]:
                fields(body, ("expected_version", "annotation_id", "disposition", "text"))
                result = reviews.resolve_annotation(graph_id, body["annotation_id"], body["disposition"], body["text"], body["expected_version"])
            elif route == ["reopen"]:
                fields(body, ("expected_version", "annotation_id"))
                result = reviews.reopen_annotation(graph_id, body["annotation_id"], body["expected_version"])
            elif route == ["request-review"]:
                fields(body, ("expected_version",))
                result = reviews.request_review(graph_id, body["expected_version"])
            elif route == ["approve"]:
                fields(body, ("expected_version", "acknowledge_limits", "text"))
                result = reviews.approve_review(graph_id, body["text"], body["acknowledge_limits"], body["expected_version"])
            else:
                raise ReviewError("Route not found.", 404)
            self.reply(200, self.legacy_state(result) if legacy else self.public_review(result))

        def run_route(self, callback):
            try:
                callback()
            except ReviewError as error:
                self.close_connection = True
                self.reply(error.status, {"error": str(error)})
            except Exception:
                # SDK/API adapters may raise their own exception classes. Do not
                # expose their endpoints, credentials, or response bodies.
                self.close_connection = True
                self.reply(500, {"error": "Review operation failed. Inspect the configured repositories and retry; no approval was implied."})

        def do_GET(self):
            self.run_route(self.route_get)

        def do_HEAD(self):
            self.run_route(self.route_get)

        def do_POST(self):
            self.run_route(self.route_mutation)

        def do_PATCH(self):
            self.run_route(self.route_mutation)

        def do_DELETE(self):
            self.run_route(self.route_mutation)

        def do_OPTIONS(self):
            self.close_connection = True
            self.reply(405, {"error": "Cross-origin access is not enabled."})

    class Server(ThreadingHTTPServer):
        address_family = socket.AF_INET6 if address.version == 6 else socket.AF_INET
        daemon_threads = True
        allow_reuse_address = True

    server = Server((host, port), Handler)
    bound_port = server.server_address[1]
    for authority in (f"127.0.0.1:{bound_port}", f"localhost:{bound_port}", f"[::1]:{bound_port}"):
        hosts.add(authority)
        origins.add("http://" + authority)
    display_host = "[" + host + "]" if address.version == 6 else host
    local_url = f"http://{display_host}:{bound_port}"
    if allowed_origin:
        origins.add(allowed_origin)
        hosts.add(urlsplit(allowed_origin).netloc)
    return server, local_url


def build_parser():
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    for command in ("init", "serve", "status", "wait", "disconnect", "propose", "annotate", "edit-annotation", "delete-annotation", "resolve", "reopen", "complete-review", "approve-chat", "export"):
        sub = subparsers.add_parser(command)
        if command == "serve":
            storage = sub.add_mutually_exclusive_group(required=True)
            storage.add_argument("--connection", type=Path, help="Saved repository locator shared with the feedback CLI")
            storage.add_argument("--session", type=Path)
            storage.add_argument("--workspace", type=Path, help="Catalog of initialized review-session directories")
            storage.add_argument("--repository-factory", help="Trusted module:callable or /path/plugin.py:callable returning graph/review repositories")
            sub.add_argument("--repository-config", type=Path, help="JSON object passed to the custom repository factory")
        else:
            sub.add_argument("--session", required=True, type=Path)
        if command not in {"init", "serve", "status", "wait", "disconnect"}:
            sub.add_argument("--expect-version", required=True, type=int)
        if command == "init":
            sub.add_argument("--title", required=True)
        elif command == "serve":
            sub.add_argument("--host", default="127.0.0.1")
            sub.add_argument("--port", default=0, type=int)
            sub.add_argument("--allowed-origin", help="Exact origin of a trusted preview proxy; this is not user authentication")
        elif command == "wait":
            sub.add_argument("--after-version", required=True, type=int)
            sub.add_argument("--timeout", default=20, type=float, help="Wait at most 30 seconds; default 20")
        elif command == "propose":
            sub.add_argument("--graph", required=True, type=Path)
            sub.add_argument("--summary", required=True)
        elif command == "annotate":
            sub.add_argument("--target-type", required=True, choices=sorted(TARGET_TYPES))
            sub.add_argument("--target-id")
            sub.add_argument("--kind", required=True, choices=sorted(KINDS))
            sub.add_argument("--text", required=True)
            sub.add_argument("--source", required=True, choices=("chat", "agent"))
            sub.add_argument("--source-ref")
        elif command == "resolve":
            sub.add_argument("--id", required=True)
            sub.add_argument("--disposition", required=True, choices=sorted(DISPOSITIONS))
            sub.add_argument("--text", required=True)
            sub.add_argument("--source", required=True, choices=("chat", "agent"))
        elif command in {"edit-annotation", "delete-annotation"}:
            sub.add_argument("--id", required=True)
            sub.add_argument("--source", required=True, choices=("chat", "agent"))
            if command == "edit-annotation":
                sub.add_argument("--text")
                sub.add_argument("--kind", choices=sorted(KINDS))
        elif command == "reopen":
            sub.add_argument("--id", required=True)
        elif command == "complete-review":
            sub.add_argument("--summary", required=True)
        elif command == "approve-chat":
            sub.add_argument("--text", required=True)
            sub.add_argument("--source-ref", required=True)
            sub.add_argument("--acknowledge-limits", action="store_true")
        elif command == "export":
            sub.add_argument("--out", required=True, type=Path)
    validate = subparsers.add_parser("validate")
    validate.add_argument("--graph", required=True, type=Path)
    connect = subparsers.add_parser("connect", help="Save a trusted repository locator without storage credentials")
    connect.add_argument("--connection", required=True, type=Path)
    storage = connect.add_mutually_exclusive_group(required=True)
    storage.add_argument("--session", type=Path)
    storage.add_argument("--workspace", type=Path)
    storage.add_argument("--repository-factory")
    connect.add_argument("--repository-config", type=Path)
    connect.add_argument("--graph-id")
    for command in ("feedback-graphs", "feedback-status", "pending-feedback", "mark-processed"):
        sub = subparsers.add_parser(command)
        storage = sub.add_mutually_exclusive_group(required=True)
        storage.add_argument("--connection", type=Path)
        storage.add_argument("--session", type=Path, help="Explicit local session convenience")
        if command != "feedback-graphs":
            sub.add_argument("--graph-id", help="Opaque graph ID; defaults to the connection's graph_id")
        if command == "pending-feedback":
            sub.add_argument("--after-version", type=int)
            sub.add_argument("--timeout", default=0, type=float, help="Bounded wait up to 30 seconds; requires --after-version")
        elif command == "mark-processed":
            sub.add_argument("--id", required=True)
            sub.add_argument("--feedback-revision", required=True, type=int)
            sub.add_argument("--outcome", required=True, choices=sorted(PROCESSING_OUTCOMES))
            sub.add_argument("--summary", required=True)
            sub.add_argument("--processor", required=True, help="Truthful agent/run reference, not an authenticated identity")
            sub.add_argument("--expect-version", required=True, type=int)
    return parser


def connection_receipt(connection):
    return {"connection": str(connection.path) if connection.path else None,
            "repository_kind": connection.kind, "graph_id": connection.graph_id,
            "graphs": connection.repositories.graphs.list_graphs()}


def feedback_command(args):
    connection = read_connection(args.connection) if args.connection else session_connection(args.session)
    repositories = connection.repositories
    if args.command == "feedback-graphs":
        return connection_receipt(connection)
    graph_id = connection.select_graph(args.graph_id)
    if args.command == "feedback-status":
        review = repositories.reviews.get_review(graph_id)
        if review.get("graph_id") != graph_id or not isinstance(review.get("graph_revision"), str):
            raise ReviewError("Repository returned an inconsistent review identity.", 409)
        snapshot = repositories.graphs.get_graph(graph_id, review["graph_revision"])
        if (snapshot.get("id") != graph_id or snapshot.get("revision") != review["graph_revision"]
                or snapshot.get("digest") != review.get("graph_digest")):
            raise ReviewError("Review and immutable graph snapshot do not match. Refresh before processing feedback.", 409)
        return {"connection": str(connection.path) if connection.path else None,
                "graph": snapshot, "review": review}
    feedback = check_feedback_repository(repositories.reviews)
    if args.command == "mark-processed":
        return feedback.mark_feedback_processed(graph_id, args.id, args.feedback_revision,
                                                 args.outcome, args.summary, args.processor,
                                                 args.expect_version)
    if args.after_version is not None and args.after_version < 0:
        raise ReviewError("after-version must be a nonnegative integer.")
    if not math.isfinite(args.timeout) or not 0 <= args.timeout <= 30:
        raise ReviewError("timeout must be between 0 and 30 seconds.")
    if args.timeout and args.after_version is None:
        raise ReviewError("A pending-feedback wait requires --after-version.")
    deadline = time.monotonic() + args.timeout
    while True:
        pending = feedback.list_pending_feedback(graph_id)
        if (pending.get("schema_version") != "behavior-review-feedback/1"
                or pending.get("graph_id") != graph_id
                or type(pending.get("state_version")) is not int
                or not isinstance(pending.get("annotations"), list)):
            raise ReviewError("Repository returned an invalid feedback inbox.", 409)
        changed = args.after_version is not None and pending["state_version"] != args.after_version
        remaining = deadline - time.monotonic()
        if args.after_version is None or changed or remaining <= 0:
            result = {**pending, "connection": str(connection.path) if connection.path else None}
            if args.after_version is not None:
                result["changed"] = changed
            return result
        time.sleep(min(0.5, remaining))


def main(argv=None):
    args = build_parser().parse_args(argv)
    try:
        if args.command == "validate":
            graph = read_graph(args.graph)
            result = {"valid": True, "nodes": len(graph["nodes"]), "dependencies": len(graph["dependencies"]),
                      "notice": "Structure and reference integrity checked; source truth and completeness are not verified."}
        elif args.command == "connect":
            connection = write_connection(args.connection, session=args.session, workspace=args.workspace,
                                          factory=args.repository_factory, config=args.repository_config,
                                          graph_id=args.graph_id)
            result = connection_receipt(connection)
        elif args.command in {"feedback-graphs", "feedback-status", "pending-feedback", "mark-processed"}:
            result = feedback_command(args)
        elif args.command == "serve":
            if args.repository_config and not args.repository_factory:
                raise ReviewError("--repository-config requires --repository-factory.")
            connection = None
            if args.connection:
                connection = read_connection(args.connection)
                repositories = connection.repositories
            elif args.repository_factory:
                repositories = load_repositories(args.repository_factory, args.repository_config)
            elif args.workspace:
                repositories = local_repositories(workspace=args.workspace)
            else:
                repositories = Store(args.session)
                connection = write_connection(Path(args.session) / "connection.json", session=args.session)
            server, url = make_server(repositories, args.host, args.port, args.allowed_origin)
            info = {"url": url, "mode": "graph review"}
            if connection is not None:
                info["connection"] = str(connection.path)
                info["graph_id"] = connection.graph_id
            if isinstance(repositories, Store):
                info["session_id"] = repositories.state()["session_id"]
            else:
                info["graph_count"] = len(repositories.graphs.list_graphs())
            print(json.dumps(info), flush=True)
            try:
                server.serve_forever(poll_interval=0.3)
            except KeyboardInterrupt:
                pass
            finally:
                server.server_close()
            return 0
        else:
            store = Store(args.session)
            if args.command == "init":
                result = store.initialize(args.title)
                connection = write_connection(store.directory / "connection.json", session=store.directory,
                                              graph_id=result["session_id"])
                result = {**result, "connection": str(connection.path)}
            elif args.command == "status":
                result = store.state()
            elif args.command == "wait":
                result = store.wait(args.after_version, args.timeout)
            elif args.command == "disconnect":
                result = store.disconnect()
            elif args.command == "propose":
                result = store.propose(read_graph(args.graph), args.summary, args.expect_version)
            elif args.command == "annotate":
                result = store.annotate(args.target_type, args.target_id, args.kind, args.text, args.source, args.source_ref, args.expect_version)
            elif args.command == "resolve":
                result = store.resolve(args.id, args.disposition, args.text, args.source, args.expect_version)
            elif args.command == "edit-annotation":
                changes = {key: getattr(args, key) for key in ("text", "kind") if getattr(args, key) is not None}
                result = store.edit_annotation(args.id, changes, args.source, args.expect_version)
            elif args.command == "delete-annotation":
                result = store.delete_annotation(args.id, args.source, args.expect_version)
            elif args.command == "reopen":
                result = store.reopen(args.id, args.expect_version)
            elif args.command == "complete-review":
                result = store.complete_review(args.summary, args.expect_version)
            elif args.command == "approve-chat":
                result = store.approve(args.text, "chat", args.source_ref, args.acknowledge_limits, args.expect_version)
            elif args.command == "export":
                result = store.export(args.out, args.expect_version)
        print(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False))
        return 0
    except ReviewError as error:
        print(json.dumps({"error": str(error)}), file=sys.stderr)
        return 1
    except (OSError, sqlite3.Error):
        print(json.dumps({"error": "Review storage operation failed. Check the connection and storage access; processing was not confirmed."}), file=sys.stderr)
        return 1
    except Exception:
        # Custom repository SDKs can include tokens or response bodies in errors.
        print(json.dumps({"error": "Repository operation failed. Check the configured adapter; processing was not confirmed."}), file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
