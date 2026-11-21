"""Remote adapter boundaries: conditional S3 writes and real local HTTP calls.

These tests use a stateful S3 protocol fake, never a live AWS account. HTTP tests
use an in-process service plus the actual shipped review server/domain model.
"""

from contextlib import contextmanager
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from urllib.parse import parse_qs, quote, unquote, urlsplit


SKILL = Path(os.environ.get("BEHAVIOR_REVIEW_SKILL_DIR", os.environ.get(
    "BEHAVIOR_SKILL_DIR", Path(__file__).resolve().parents[1] / "skills" / "behavior-graph-review-app")))
sys.path.insert(0, str(SKILL / "scripts"))
sys.dont_write_bytecode = True
from graph_model import ReviewError, Store
from graph_repositories import local_repositories
from http_repository import HttpGraphRepository, HttpHumanReviewRepository, HttpRepositoryClient
from s3_repository import S3DocumentStore
from test_graph_repositories import app_dist, running_server


class S3Failure(Exception):
    def __init__(self, code, status):
        super().__init__("PRIVATE-SDK-ERROR: credentials and endpoint must not be exposed")
        self.response = {"Error": {"Code": code, "Message": str(self)},
                         "ResponseMetadata": {"HTTPStatusCode": status}}


class ConditionalS3:
    """A locked object service: a stale write actually fails and preserves data."""
    def __init__(self):
        self.documents = {}
        self.requests = []
        self.failure = None
        self.response = None
        self.lock = threading.Lock()

    def get_object(self, **arguments):
        with self.lock:
            self.requests.append(("get", arguments))
            if self.failure:
                raise self.failure
            if self.response is not None:
                return self.response
            stored = self.documents.get((arguments["Bucket"], arguments["Key"]))
            if stored is None:
                raise S3Failure("NoSuchKey", 404)
            raw, token = stored
            return {"Body": io.BytesIO(raw), "ETag": token, "ContentLength": len(raw)}

    def put_object(self, **arguments):
        with self.lock:
            self.requests.append(("put", arguments))
            if self.failure:
                raise self.failure
            key = (arguments["Bucket"], arguments["Key"])
            stored = self.documents.get(key)
            if arguments.get("IfNoneMatch") == "*" and "IfMatch" not in arguments:
                if stored is not None:
                    raise S3Failure("PreconditionFailed", 412)
            elif "IfMatch" in arguments and "IfNoneMatch" not in arguments:
                if stored is None:
                    raise S3Failure("NoSuchKey", 404)
                if stored[1] != arguments["IfMatch"]:
                    raise S3Failure("PreconditionFailed", 412)
            else:
                raise AssertionError("Unconditional writes are forbidden in this fixture")
            raw = arguments["Body"]
            token = '"' + hashlib.sha256(raw).hexdigest() + '"'
            self.documents[key] = (raw, token)
            return {"ETag": token}


class S3RepositoryTests(unittest.TestCase):
    def setUp(self):
        self.service = ConditionalS3()
        self.store = S3DocumentStore("test-bucket", "reviews/team/", client=self.service)

    def test_conditional_create_and_utf8_round_trip(self):
        document = {"state": {"text": "確認しました — проверено", "state_version": 1}, "audit": []}
        self.store.create("reviews/id.json", document)
        returned, token = self.store.read("reviews/id.json")
        self.assertEqual(returned, document)
        self.assertTrue(token.startswith('"'))
        operation, arguments = self.service.requests[0]
        self.assertEqual(operation, "put")  # Create is one conditional write, not a read/write race.
        self.assertEqual(arguments["Key"], "reviews/team/reviews/id.json")
        self.assertEqual(arguments["IfNoneMatch"], "*")
        self.assertNotIn("IfMatch", arguments)
        self.assertIn("確認".encode("utf-8"), arguments["Body"])
        with self.assertRaises(ReviewError) as caught:
            self.store.create("reviews/id.json", {"state": "overwrite"})
        self.assertEqual(caught.exception.status, 409)
        self.assertEqual(self.store.read("reviews/id.json")[0], document)

    def test_two_readers_cannot_overwrite_each_others_state_and_audit(self):
        other = S3DocumentStore("test-bucket", "reviews/team", client=self.service)
        self.store.create("reviews/id.json", {"state": {"version": 1}, "audit": []})
        _, first_token = self.store.read("reviews/id.json")
        _, second_token = other.read("reviews/id.json")
        winner = {"state": {"version": 2, "processed_revision": 1}, "audit": ["processed"]}
        loser = {"state": {"version": 2, "text": "stale edit"}, "audit": ["edited"]}
        self.store.compare_and_swap("reviews/id.json", winner, first_token)
        with self.assertRaises(ReviewError) as caught:
            other.compare_and_swap("reviews/id.json", loser, second_token)
        self.assertEqual(caught.exception.status, 409)
        self.assertEqual(self.store.read("reviews/id.json")[0], winner)
        puts = [arguments for method, arguments in self.service.requests if method == "put"]
        self.assertEqual(puts[1]["IfMatch"], first_token)
        self.assertEqual(puts[2]["IfMatch"], second_token)
        self.assertNotIn("IfNoneMatch", puts[1])

    def test_missing_read_is_404_and_deleted_cas_cannot_recreate(self):
        with self.assertRaises(ReviewError) as caught:
            self.store.read("missing.json")
        self.assertEqual(caught.exception.status, 404)
        self.assertEqual(self.service.documents, {})
        with self.assertRaises(ReviewError) as caught:
            self.store.compare_and_swap("missing.json", {"state": 1}, '"old-etag"')
        self.assertEqual(caught.exception.status, 409)
        self.assertEqual(self.service.documents, {})

    def test_conflicts_and_service_errors_preserve_status_without_sdk_secrets(self):
        for code, status, expected in (("ConditionalRequestConflict", 409, 409),
                                        ("PreconditionFailed", 412, 409),
                                        ("AccessDenied", 403, 403), ("Unavailable", 503, 502),
                                        ("NoSuchBucket", 404, 502)):
            with self.subTest(code=code):
                self.service.failure = S3Failure(code, status)
                with self.assertRaises(ReviewError) as caught:
                    self.store.create("catalog.json", {})
                self.assertEqual(caught.exception.status, expected)
                self.assertNotIn("PRIVATE-SDK-ERROR", str(caught.exception))

    def test_read_size_utf8_and_json_validation_close_streams(self):
        small = S3DocumentStore("bucket", client=self.service, max_bytes=32)
        for raw, size, expected in ((b"x" * 33, 33, 413), (b"x" * 33, None, 413),
                                    (b"\xff", 1, 502), (b"[]", 2, 502),
                                    (b'{"PRIVATE":1,"PRIVATE":2}', None, 502)):
            with self.subTest(raw=raw):
                stream = io.BytesIO(raw)
                self.service.response = {"Body": stream, "ContentLength": size, "ETag": '"v1"'}
                with self.assertRaises(ReviewError) as caught:
                    small.read("catalog.json")
                self.assertEqual(caught.exception.status, expected)
                self.assertTrue(stream.closed)
                self.assertNotIn("PRIVATE", str(caught.exception))

    def test_write_bounds_and_wildcard_tokens_fail_before_storage(self):
        small = S3DocumentStore("bucket", client=self.service, max_bytes=16)
        for document in ({"large": "x" * 16}, {"invalid": float("nan")}, []):
            with self.subTest(document=document), self.assertRaises(ReviewError):
                small.create("catalog.json", document)
        for token in ("*", '"a","b"', "", "a\r\nb"):
            with self.subTest(token=token), self.assertRaises(ReviewError):
                small.compare_and_swap("catalog.json", {}, token)
        self.assertEqual(self.service.requests, [])

    def test_scoped_relative_keys_and_secure_endpoint_configuration(self):
        for key in ("../catalog.json", "/catalog.json", "a//b", "a/../b", "a\\b", "https://host/key"):
            with self.subTest(key=key), self.assertRaises(ReviewError):
                self.store.create(key, {})
        for prefix in ("/", "/team", "../team", "a//b"):
            with self.subTest(prefix=prefix), self.assertRaises(ReviewError):
                S3DocumentStore("bucket", prefix, client=self.service)
        for endpoint in ("http://s3.example.test", "https://token@example.test", "https://host/?token=PRIVATE", "https://host?", "https://host#"):
            with self.subTest(endpoint=endpoint), self.assertRaises(ReviewError):
                S3DocumentStore("bucket", endpoint_url=endpoint, client=self.service)
        S3DocumentStore("bucket", endpoint_url="http://127.0.0.1:9000", client=self.service)
        S3DocumentStore("bucket", endpoint_url="https://s3.example.test", client=self.service)
        self.assertEqual(self.service.requests, [])

    def test_optional_sdk_uses_profile_chain_and_signature_v4(self):
        calls = {}
        def session(**arguments):
            calls["session"] = arguments
            def client(name, **options):
                calls["client"] = (name, options)
                return self.service
            return SimpleNamespace(client=client)
        fake_modules = {"boto3": SimpleNamespace(Session=session),
                        "botocore": SimpleNamespace(),
                        "botocore.config": SimpleNamespace(Config=lambda **options: options)}
        with patch.dict(sys.modules, fake_modules):
            store = S3DocumentStore("bucket", region="eu-west-1", profile="review-team")
        self.assertIs(store.client, self.service)
        self.assertEqual(calls["session"], {"region_name": "eu-west-1", "profile_name": "review-team"})
        self.assertEqual(calls["client"][0], "s3")
        self.assertEqual(calls["client"][1]["config"]["signature_version"], "s3v4")
        self.assertEqual(calls["client"][1]["config"]["retries"]["total_max_attempts"], 1)


@contextmanager
def http_service(callback):
    """Expose callback(method,path,headers,JSONbody)->(status,body,headers)."""
    requests = []
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *arguments):
            pass
        def handle_request(self):
            data = self.rfile.read(int(self.headers.get("Content-Length", 0)))
            body = json.loads(data.decode("utf-8")) if data else None
            record = (self.command, self.path, dict(self.headers), body)
            requests.append(record)
            status, payload, headers = callback(*record)
            raw = payload if isinstance(payload, bytes) else json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            for key, value in headers.items():
                self.send_header(key, value)
            self.end_headers()
            self.wfile.write(raw)
        do_GET = do_POST = do_PATCH = do_DELETE = handle_request
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True)
    thread.start()
    try:
        yield SimpleNamespace(url=f"http://127.0.0.1:{server.server_port}", requests=requests)
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)


class HttpTransportTests(unittest.TestCase):
    def test_opaque_ids_revisions_and_reverse_proxy_prefix(self):
        identifier, revision, annotation_id = "team:booking/確認?#", "etag/x y&z", "note/id?one"
        snapshot = {"id": identifier, "revision": revision, "graph": {"nodes": []}}
        def respond(method, path, headers, body):
            parsed = urlsplit(path)
            if parsed.path == "/review-service/api/graphs":
                return 200, {"graphs": [{"id": identifier, "revision": revision}]}, {}
            pieces = [unquote(part) for part in parsed.path.split("/")]
            self.assertEqual(pieces[4], identifier)
            if pieces[-1] == annotation_id:
                self.assertEqual(parse_qs(parsed.query), {"include_deleted": ["true"]})
                return 200, {"id": annotation_id, "status": "deleted"}, {}
            self.assertEqual(parse_qs(parsed.query), {"revision": [revision]})
            return 200, snapshot, {}
        with http_service(respond) as remote:
            client = HttpRepositoryClient(remote.url + "/review-service/")
            graphs, reviews = HttpGraphRepository(client), HttpHumanReviewRepository(client)
            self.assertEqual(graphs.list_graphs()[0]["id"], identifier)
            self.assertEqual(graphs.get_graph(identifier, revision), snapshot)
            self.assertEqual(reviews.get_annotation(identifier, annotation_id, True)["status"], "deleted")
            self.assertIn(quote(identifier, safe=""), remote.requests[1][1])
            self.assertEqual(client.origin, remote.url)

    def test_mutation_auth_csrf_origin_and_version_travel_together(self):
        def respond(method, path, headers, body):
            self.assertEqual(headers.get("Authorization"), "Bearer test-private-token")
            if path == "/prefix/api/bootstrap":
                self.assertEqual(method, "GET")
                return 200, {"csrf_token": "review-test-token"}, {}
            self.assertEqual(headers.get("X-Review-Token"), "review-test-token")
            self.assertEqual(headers.get("Origin"), remote.url)
            self.assertEqual(body["expected_version"], 7)
            self.assertEqual(body["summary"], "検証済み")
            self.assertEqual(path, "/prefix/api/graphs/graph%2Fone/mark-processed")
            return 200, {"state_version": 8}, {}
        with http_service(respond) as remote, patch.dict(os.environ, {"REVIEW_TEST_TOKEN": "test-private-token"}):
            reviews = HttpHumanReviewRepository(HttpRepositoryClient(remote.url + "/prefix", "REVIEW_TEST_TOKEN"))
            result = reviews.mark_feedback_processed("graph/one", "note/id", 2, "addressed", "検証済み", "test", 7)
            self.assertEqual(result["state_version"], 8)
            self.assertEqual(len(remote.requests), 2)

    def test_redirects_never_forward_auth_or_mutations(self):
        with http_service(lambda *arguments: (200, {}, {})) as destination:
            for code in (302, 303, 307, 308):
                def respond(method, path, headers, body):
                    if path == "/api/bootstrap":
                        return 200, {"csrf_token": "test-csrf"}, {}
                    return code, {"error": "PRIVATE-HTTP-ERROR"}, {"Location": destination.url + "/trap"}
                with self.subTest(code=code), http_service(respond) as source, patch.dict(os.environ, {"REVIEW_TEST_TOKEN": "test-secret"}):
                    reviews = HttpHumanReviewRepository(HttpRepositoryClient(source.url, "REVIEW_TEST_TOKEN"))
                    with self.assertRaises(ReviewError) as caught:
                        reviews.request_review("graph", 2)
                    self.assertEqual(caught.exception.status, 502)
                    self.assertNotIn("PRIVATE-HTTP-ERROR", str(caught.exception))
            self.assertEqual(destination.requests, [])

    def test_remote_errors_preserve_conflicts_missing_and_capability_statuses(self):
        for status in (400, 401, 403, 404, 409, 501, 503):
            with self.subTest(status=status), http_service(lambda *arguments: (status, {"error": "PRIVATE-HTTP-ERROR"}, {})) as remote:
                with self.assertRaises(ReviewError) as caught:
                    HttpHumanReviewRepository(HttpRepositoryClient(remote.url)).list_pending_feedback("graph")
                self.assertEqual(caught.exception.status, status)
                self.assertNotIn("PRIVATE-HTTP-ERROR", str(caught.exception))

    def test_malformed_or_oversized_responses_fail_without_exposing_contents(self):
        for payload in (b'{"PRIVATE":1,"PRIVATE":2}', b"\xff", b"[]", {"oversized": "PRIVATE" * 100}):
            with self.subTest(payload=payload), http_service(lambda *arguments: (200, payload, {})) as remote:
                with self.assertRaises(ReviewError) as caught:
                    HttpGraphRepository(HttpRepositoryClient(remote.url, max_bytes=64)).list_graphs()
                self.assertEqual(caught.exception.status, 502)
                self.assertNotIn("PRIVATE", str(caught.exception))

    def test_missing_auth_and_invalid_configuration_fail_without_network(self):
        with http_service(lambda *arguments: (200, {}, {})) as remote, patch.dict(os.environ, {}, clear=True):
            with self.assertRaises(ReviewError) as caught:
                HttpGraphRepository(HttpRepositoryClient(remote.url, "MISSING_REVIEW_TOKEN")).list_graphs()
            self.assertEqual(caught.exception.status, 401)
            self.assertEqual(remote.requests, [])
        for url in ("http://remote.example.test", "https://PRIVATE@remote.example.test", "https://host/?token=PRIVATE", "https://host/#token", "https://host?", "https://host#"):
            with self.subTest(url=url), self.assertRaises(ReviewError) as caught:
                HttpRepositoryClient(url)
            self.assertNotIn("PRIVATE", str(caught.exception))
        HttpRepositoryClient("https://service.example.test/review")
        HttpRepositoryClient("http://[::1]:9000")

    def test_invalid_or_large_request_does_not_bootstrap_or_write(self):
        with http_service(lambda *arguments: (200, {}, {})) as remote:
            reviews = HttpHumanReviewRepository(HttpRepositoryClient(remote.url))
            for value in ({"text": "x" * (128 * 1024)}, {"text": float("nan")}, []):
                with self.subTest(value_type=type(value)), self.assertRaises(ReviewError):
                    reviews.create_annotation("graph", value, 1)
            self.assertEqual(remote.requests, [])


class HttpActualServerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.store = Store(self.root / "session")
        self.store.initialize("HTTP repository fixture")
        self.graph = json.loads((SKILL / "assets" / "example-graph.json").read_text())
        self.store.propose(self.graph, "Initial proposal", 1)
        self.repositories = local_repositories(session=self.store)
        self.graph_id = self.store.state()["session_id"]
        self.dist = app_dist(self.root / "dist")

    def test_full_review_crud_and_processing_over_actual_service(self):
        with running_server(self.repositories, self.dist) as url:
            client = HttpRepositoryClient(url)
            graphs, reviews = HttpGraphRepository(client), HttpHumanReviewRepository(client)
            self.assertEqual(graphs.list_graphs()[0]["id"], self.graph_id)
            self.assertEqual(graphs.get_graph(self.graph_id, "1")["graph"], self.graph)
            state = reviews.create_annotation(self.graph_id, {
                "target_type": "graph", "target_id": None, "kind": "question", "text": "How does retry behave?",
            }, 2)
            note = state["annotations"][0]
            self.assertEqual(reviews.get_annotation(self.graph_id, note["id"]), note)
            self.assertEqual(reviews.list_pending_feedback(self.graph_id)["annotations"], [note])
            state = reviews.update_annotation(self.graph_id, note["id"], {"text": "Retry twice — 確認?"}, state["state_version"])
            with self.assertRaises(ReviewError) as caught:
                reviews.mark_feedback_processed(self.graph_id, note["id"], 1, "addressed", "Stale feedback", "test", state["state_version"])
            self.assertEqual(caught.exception.status, 409)
            state = reviews.resolve_annotation(self.graph_id, note["id"], "addressed", "Retry exactly twice.", state["state_version"])
            current = state["annotations"][0]
            state = reviews.mark_feedback_processed(self.graph_id, note["id"], current["feedback_revision"], "addressed", "Recorded retry limit", "test", state["state_version"])
            self.assertEqual(reviews.list_pending_feedback(self.graph_id)["annotations"], [])
            state = reviews.approve_review(self.graph_id, "Scope and limits reviewed.", True, state["state_version"])
            self.assertIsNotNone(state["approval"])
            state = reviews.reopen_annotation(self.graph_id, note["id"], state["state_version"])
            self.assertIsNone(state["approval"])
            state = reviews.delete_annotation(self.graph_id, note["id"], state["state_version"])
            self.assertEqual(reviews.list_annotations(self.graph_id), [])
            self.assertEqual(reviews.list_annotations(self.graph_id, True)[0]["status"], "deleted")
            with self.assertRaises(ReviewError) as caught:
                reviews.get_annotation(self.graph_id, note["id"])
            self.assertEqual(caught.exception.status, 404)
            self.assertEqual(reviews.get_annotation(self.graph_id, note["id"], True)["status"], "deleted")
            state = reviews.request_review(self.graph_id, state["state_version"])
            tombstone = reviews.list_pending_feedback(self.graph_id)["annotations"][0]
            state = reviews.mark_feedback_processed(self.graph_id, note["id"], tombstone["feedback_revision"], "withdrawn", "Acknowledged deletion", "test", state["state_version"])
            self.assertTrue(state["review_requested"])
            self.assertEqual(reviews.list_pending_feedback(self.graph_id)["annotations"], [])
            self.assertEqual(reviews.get_review(self.graph_id)["state_version"], self.store.state()["state_version"])

    def test_two_http_clients_cannot_apply_same_review_version(self):
        with running_server(self.repositories, self.dist) as url:
            first, second = [HttpHumanReviewRepository(HttpRepositoryClient(url)) for _ in range(2)]
            version = first.get_review(self.graph_id)["state_version"]
            self.assertEqual(second.get_review(self.graph_id)["state_version"], version)
            note = {"target_type": "graph", "target_id": None, "kind": "question", "text": "Concurrent note"}
            first.create_annotation(self.graph_id, note, version)
            with self.assertRaises(ReviewError) as caught:
                second.create_annotation(self.graph_id, note, version)
            self.assertEqual(caught.exception.status, 409)
            self.assertEqual(len(first.list_annotations(self.graph_id)), 1)


if __name__ == "__main__":
    unittest.main()
