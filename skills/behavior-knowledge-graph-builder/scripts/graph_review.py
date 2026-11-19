#!/usr/bin/env python3
"""Local, persistent behavior-graph review. Python 3.10+; no third-party packages."""

import argparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import ipaddress
import json
from pathlib import Path
import secrets
import socket
import sqlite3
import sys
from urllib.parse import parse_qs, urlsplit

sys.dont_write_bytecode = True

from graph_model import KINDS, DISPOSITIONS, TARGET_TYPES, Store, ReviewError, parse_json, read_graph


MAX_REQUEST_BYTES = 128 * 1024
VIEWER = Path(__file__).resolve().parent.parent / "assets" / "review.html"


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


def make_server(store, host="127.0.0.1", port=0, allowed_origin=None):
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
    store.state()  # Fail before binding if the session is missing.
    token = store.token()
    if not VIEWER.is_file():
        raise ReviewError("Packaged review frontend was not found.")
    viewer = VIEWER.read_bytes()
    origins = set()
    hosts = set()

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"
        server_version = "BehaviorReview/1"
        sys_version = ""

        def log_message(self, format, *args):
            # Do not put project text, annotations, or authentication material in logs.
            pass

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
            self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; object-src 'none'; base-uri 'none'")
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

        def public_state(self, state=None):
            result = state if state is not None else store.state()
            return {**result, "csrf_token": token}

        def route_get(self):
            self.check_origin()
            parsed = urlsplit(self.path)
            if parsed.path == "/":
                self.reply(200, viewer, "text/html; charset=utf-8")
            elif parsed.path == "/api/state":
                self.reply(200, self.public_state())
            elif parsed.path == "/api/health":
                self.reply(200, {"ok": True, "session_id": store.state()["session_id"]})
            elif parsed.path == "/api/round":
                query = parse_qs(parsed.query)
                if set(query) != {"round"} or len(query["round"]) != 1:
                    raise ReviewError("Provide one round number.")
                try:
                    number = int(query["round"][0])
                except ValueError as error:
                    raise ReviewError("Invalid round number.") from error
                self.reply(200, store.round_snapshot(number))
            elif parsed.path == "/favicon.ico":
                self.reply(204, b"", "image/x-icon")
            else:
                raise ReviewError("Route not found.", 404)

        def route_post(self):
            self.check_origin(mutation=True)
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
                body = parse_json(raw.decode("utf-8"))
            except UnicodeError as error:
                raise ReviewError("Request must be UTF-8 JSON.") from error
            parsed = urlsplit(self.path)
            if parsed.query:
                raise ReviewError("Mutation routes do not accept query parameters.")
            if parsed.path == "/api/annotations":
                fields(body, ("expected_version", "target_type", "target_id", "kind", "text"))
                result = store.annotate(body["target_type"], body["target_id"], body["kind"], body["text"], "ui", None, body["expected_version"])
            elif parsed.path == "/api/resolve":
                fields(body, ("expected_version", "annotation_id", "disposition", "text"))
                result = store.resolve(body["annotation_id"], body["disposition"], body["text"], "ui", body["expected_version"])
            elif parsed.path == "/api/reopen":
                fields(body, ("expected_version", "annotation_id"))
                result = store.reopen(body["annotation_id"], body["expected_version"], "ui")
            elif parsed.path == "/api/request-review":
                fields(body, ("expected_version",))
                result = store.request_review(body["expected_version"])
            elif parsed.path == "/api/approve":
                fields(body, ("expected_version", "acknowledge_limits", "text"))
                result = store.approve(body["text"], "ui", None, body["acknowledge_limits"], body["expected_version"])
            else:
                raise ReviewError("Route not found.", 404)
            self.reply(200, self.public_state(result))

        def run_route(self, callback):
            try:
                callback()
            except ReviewError as error:
                self.close_connection = True
                self.reply(error.status, {"error": str(error)})
            except (OSError, sqlite3.Error, ValueError) as error:
                self.close_connection = True
                # Keep filesystem/database internals out of browser errors.
                self.reply(500, {"error": "Review operation failed. Inspect the session and retry; no approval was implied."})

        def do_GET(self):
            self.run_route(self.route_get)

        def do_HEAD(self):
            self.run_route(self.route_get)

        def do_POST(self):
            self.run_route(self.route_post)

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
    for command in ("init", "serve", "status", "wait", "disconnect", "propose", "annotate", "resolve", "reopen", "complete-review", "approve-chat", "export"):
        sub = subparsers.add_parser(command)
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
    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    try:
        if args.command == "validate":
            graph = read_graph(args.graph)
            result = {"valid": True, "nodes": len(graph["nodes"]), "dependencies": len(graph["dependencies"]),
                      "notice": "Structure and reference integrity checked; source truth and completeness are not verified."}
        else:
            store = Store(args.session)
            if args.command == "init":
                result = store.initialize(args.title)
            elif args.command == "status":
                result = store.state()
            elif args.command == "wait":
                result = store.wait(args.after_version, args.timeout)
            elif args.command == "disconnect":
                result = store.disconnect()
            elif args.command == "serve":
                server, url = make_server(store, args.host, args.port, args.allowed_origin)
                print(json.dumps({"url": url, "session_id": store.state()["session_id"], "mode": "local review"}), flush=True)
                try:
                    server.serve_forever(poll_interval=0.3)
                except KeyboardInterrupt:
                    pass
                finally:
                    server.server_close()
                return 0
            elif args.command == "propose":
                result = store.propose(read_graph(args.graph), args.summary, args.expect_version)
            elif args.command == "annotate":
                result = store.annotate(args.target_type, args.target_id, args.kind, args.text, args.source, args.source_ref, args.expect_version)
            elif args.command == "resolve":
                result = store.resolve(args.id, args.disposition, args.text, args.source, args.expect_version)
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
    except (ReviewError, OSError, sqlite3.Error) as error:
        print(json.dumps({"error": str(error)}), file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
