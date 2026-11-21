"""Repository, annotation workflow, and HTTP regressions; Python stdlib only."""

from contextlib import contextmanager
from copy import deepcopy
import hashlib
import http.client
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch
from urllib.parse import quote, urlsplit


SKILL = Path(os.environ.get("BEHAVIOR_REVIEW_SKILL_DIR", os.environ.get(
    "BEHAVIOR_SKILL_DIR", Path(__file__).resolve().parents[1] / "skills" / "behavior-graph-review-app")))
sys.path.insert(0, str(SKILL / "scripts"))
sys.dont_write_bytecode = True
from graph_model import Store, ReviewError, digest
from graph_repositories import Repositories, local_repositories, load_repositories
from graph_review import make_server, load_dist


def app_dist(path):
    path.mkdir()
    contents = {"index.html": b'<!doctype html><script src="/app-123.js"></script>',
                "app-123.js": b'window.fixture = true;', "app-123.css": b'body{color:black}'}
    for name, data in contents.items():
        (path / name).write_bytes(data)
    (path / "manifest.json").write_text(json.dumps({
        "schema_version": "behavior-review-app-dist/1", "entry": "index.html",
        "files": {name: hashlib.sha256(data).hexdigest() for name, data in contents.items()},
    }))
    return path


@contextmanager
def running_server(repositories, dist):
    server, url = make_server(repositories, dist=dist)
    thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True)
    thread.start()
    try:
        yield url
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)


def request(url, path, method="GET", body=None, token=None, origin=True, extra_headers=None):
    parsed = urlsplit(url)
    connection = http.client.HTTPConnection(parsed.hostname, parsed.port, timeout=3)
    headers = {}
    if method in {"POST", "PATCH", "DELETE"}:
        headers["Content-Type"] = "application/json"
        if origin:
            headers["Origin"] = url
        if token:
            headers["X-Review-Token"] = token
    headers.update(extra_headers or {})
    connection.request(method, path, body=None if body is None else json.dumps(body), headers=headers)
    response = connection.getresponse()
    raw = response.read()
    status, returned_headers = response.status, dict(response.getheaders())
    connection.close()
    if returned_headers.get("Content-Type", "").startswith("application/json"):
        raw = json.loads(raw)
    return status, raw, returned_headers


class MemoryGraphs:
    """A genuinely independent in-memory read adapter with opaque IDs/revisions."""
    def __init__(self, graph):
        self.snapshot = {"id": "remote:booking/one", "title": "Remote graph", "revision": "etag-v3/xyz",
                         "digest": digest(graph), "graph": deepcopy(graph)}

    def list_graphs(self):
        return [{k: v for k, v in self.snapshot.items() if k != "graph"}]

    def get_graph(self, graph_id, revision=None):
        if graph_id != self.snapshot["id"] or revision not in {None, self.snapshot["revision"]}:
            raise ReviewError("Graph revision not found.", 404)
        return deepcopy(self.snapshot)


class MemoryReviews:
    def __init__(self, snapshot):
        self.state = {"graph_id": snapshot["id"], "graph_revision": snapshot["revision"],
                      "session_id": "remote-review", "title": snapshot["title"], "state_version": 1,
                      "round": 1, "graph_digest": snapshot["digest"], "annotations": [],
                      "rounds": [{"round": 1, "graph_revision": snapshot["revision"],
                                  "digest": snapshot["digest"], "summary": "Remote proposal"}],
                      "approval": None, "finalized": None, "review_requested": False, "agent_active": False}

    def get_review(self, graph_id):
        if graph_id != self.state["graph_id"]:
            raise ReviewError("Review not found.", 404)
        return deepcopy(self.state)

    def list_annotations(self, graph_id, include_deleted=False):
        return [x for x in self.get_review(graph_id)["annotations"] if include_deleted or x["status"] != "deleted"]

    def get_annotation(self, graph_id, annotation_id, include_deleted=False):
        return next(x for x in self.list_annotations(graph_id, include_deleted) if x["id"] == annotation_id)

    def _write(self, graph_id, expected_version):
        self.get_review(graph_id)
        if expected_version != self.state["state_version"]:
            raise ReviewError("Stale remote review.", 409)
        self.state["state_version"] += 1
        self.state["approval"] = None
        self.state["finalized"] = None

    def create_annotation(self, graph_id, annotation, expected_version):
        self._write(graph_id, expected_version)
        self.state["annotations"].append({**annotation, "id": "remote-note", "status": "open"})
        return self.get_review(graph_id)

    def update_annotation(self, graph_id, annotation_id, changes, expected_version):
        self._write(graph_id, expected_version)
        next(x for x in self.state["annotations"] if x["id"] == annotation_id).update(changes)
        return self.get_review(graph_id)

    def delete_annotation(self, graph_id, annotation_id, expected_version):
        self._write(graph_id, expected_version)
        next(x for x in self.state["annotations"] if x["id"] == annotation_id)["status"] = "deleted"
        return self.get_review(graph_id)

    def resolve_annotation(self, *args):
        raise ReviewError("Not enabled in this test adapter.", 405)

    reopen_annotation = resolve_annotation
    request_review = resolve_annotation
    approve_review = resolve_annotation


class RepositoryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.workspace = self.root / "workspace"
        self.workspace.mkdir()
        self.graph = json.loads((SKILL / "assets" / "example-graph.json").read_text())
        self.first = Store(self.workspace / "first")
        self.second = Store(self.workspace / "second")
        self.first.initialize("First review")
        self.second.initialize("Second review")
        self.first.propose(self.graph, "First proposal", 1)
        self.second.propose(self.graph, "Second proposal", 1)
        self.first_id = self.first.state()["session_id"]
        self.second_id = self.second.state()["session_id"]
        self.repositories = local_repositories(workspace=self.workspace)
        self.dist = app_dist(self.root / "dist")

    def note(self, repository=None, graph_id=None):
        repository = repository or self.repositories.reviews
        graph_id = graph_id or self.first_id
        return repository.create_annotation(graph_id, {"target_type": "graph", "target_id": None,
                                                       "kind": "correction", "text": "Original note"},
                                            repository.get_review(graph_id)["state_version"])

    def test_catalog_is_multi_graph_and_scoped(self):
        summaries = self.repositories.graphs.list_graphs()
        self.assertEqual({x["id"] for x in summaries}, {self.first_id, self.second_id})
        state = self.note()
        self.assertNotIn("graph", state)
        self.assertEqual(state["graph_id"], self.first_id)
        self.assertEqual(state["graph_revision"], "1")
        self.assertEqual(self.repositories.reviews.list_annotations(self.second_id), [])
        with self.assertRaises(ReviewError) as context:
            self.repositories.reviews.delete_annotation(self.second_id, state["annotations"][0]["id"], 2)
        self.assertEqual(context.exception.status, 404)

    def test_discovery_does_not_recurse_follow_links_or_create_sessions(self):
        nested = self.workspace / "nested"
        nested.mkdir()
        Store(nested / "hidden").initialize("Nested ignored")
        (self.workspace / "symlink").symlink_to(self.first.directory, target_is_directory=True)
        (self.workspace / "file.txt").write_text("Unrelated file")
        self.assertEqual(len(self.repositories.graphs.list_graphs()), 2)
        empty = self.root / "empty"
        empty.mkdir()
        self.assertEqual(local_repositories(workspace=empty).graphs.list_graphs(), [])
        self.assertEqual(list(empty.iterdir()), [])

    def test_duplicate_catalog_ids_fail_visibly(self):
        shutil.copytree(self.first.directory, self.workspace / "duplicate")
        with self.assertRaisesRegex(ReviewError, "Duplicate graph IDs"):
            self.repositories.graphs.list_graphs()

    def test_revision_reads_are_pinned_and_initial_revision_stays_empty(self):
        original = self.repositories.graphs.get_graph(self.first_id, "1")
        changed = deepcopy(self.graph)
        changed["nodes"][0]["summary"] += " Revised."
        self.first.propose(changed, "Revision", 2)
        self.assertEqual(self.repositories.graphs.get_graph(self.first_id, "1"), original)
        self.assertEqual(self.repositories.graphs.get_graph(self.first_id)["revision"], "2")
        self.assertIsNone(self.repositories.graphs.get_graph(self.first_id, "0")["graph"])
        self.assertEqual(original["digest"], digest(original["graph"]))
        for invalid in ("01", "-1", "1.0", "9" * 30):
            with self.assertRaises(ReviewError):
                self.repositories.graphs.get_graph(self.first_id, invalid)

    def test_edit_preserves_identity_reopens_resolution_and_invalidates_approval(self):
        state = self.note()
        note = state["annotations"][0]
        resolved = self.repositories.reviews.resolve_annotation(self.first_id, note["id"], "addressed", "Resolved", state["state_version"])
        approved = self.repositories.reviews.approve_review(self.first_id, "Approve fixture", True, resolved["state_version"])
        edited = self.repositories.reviews.update_annotation(self.first_id, note["id"], {"text": "Corrected note", "kind": "question"}, approved["state_version"])
        result = edited["annotations"][0]
        for key in ("id", "target_type", "target_id", "created_at", "source", "round"):
            self.assertEqual(result[key], note[key])
        self.assertEqual(result["text"], "Corrected note")
        self.assertEqual(result["status"], "open")
        self.assertIsNone(result["resolution"])
        self.assertIsNone(edited["approval"])
        self.assertIsNone(edited["finalized"])
        with self.first.connection() as connection:
            events = [json.loads(row[0]) for row in connection.execute("SELECT content FROM audit ORDER BY id")]
        event = events[-1]
        self.assertEqual(event["action"], "annotation_edited")
        self.assertEqual(event["payload"]["previous"]["text"], "Original note")
        self.assertEqual(event["payload"]["previous"]["status"], "resolved")

    def test_round_metadata_maps_display_rounds_to_immutable_revisions(self):
        changed = deepcopy(self.graph)
        changed["nodes"][0]["summary"] += " Next proposal."
        self.first.propose(changed, "Next proposal", 2)
        review = self.repositories.reviews.get_review(self.first_id)
        self.assertEqual([item["graph_revision"] for item in review["rounds"]], ["1", "2"])
        for item in review["rounds"]:
            snapshot = self.repositories.graphs.get_graph(self.first_id, item["graph_revision"])
            self.assertEqual(snapshot["digest"], item["digest"])
        # The adapter adds its reference; old session data requires no migration.
        self.assertTrue(all("graph_revision" not in item for item in self.first.state()["rounds"]))

    def test_delete_tombstones_retains_audit_and_cannot_reopen(self):
        state = self.note()
        note_id = state["annotations"][0]["id"]
        deleted = self.repositories.reviews.delete_annotation(self.first_id, note_id, state["state_version"])
        self.assertEqual(self.repositories.reviews.list_annotations(self.first_id), [])
        tombstone = self.repositories.reviews.get_annotation(self.first_id, note_id, include_deleted=True)
        self.assertEqual(tombstone["text"], "Original note")
        self.assertEqual(tombstone["status"], "deleted")
        with self.assertRaises(ReviewError):
            self.repositories.reviews.get_annotation(self.first_id, note_id)
        with self.assertRaises(ReviewError):
            self.repositories.reviews.reopen_annotation(self.first_id, note_id, deleted["state_version"])
        with self.assertRaises(ReviewError):
            self.repositories.reviews.update_annotation(self.first_id, note_id, {"text": "revive"}, deleted["state_version"])
        changed = deepcopy(self.graph)
        changed["nodes"][0]["summary"] += " New target."
        proposed = self.first.propose(changed, "New proposal", deleted["state_version"])
        self.assertEqual(proposed["annotations"][0]["status"], "deleted")
        approved = self.first.approve("Approve fixture", "ui", None, True, proposed["state_version"])
        self.assertIsNotNone(approved["approval"])

    def test_stale_edits_and_invalid_patch_do_not_mutate_state(self):
        state = self.note()
        note_id = state["annotations"][0]["id"]
        updated = self.repositories.reviews.update_annotation(self.first_id, note_id, {"text": "Latest"}, state["state_version"])
        with self.assertRaises(ReviewError) as context:
            self.repositories.reviews.delete_annotation(self.first_id, note_id, state["state_version"])
        self.assertEqual(context.exception.status, 409)
        for invalid in ({}, {"source": "agent"}, {"text": " "}, {"kind": "arbitrary"}):
            with self.assertRaises(ReviewError):
                self.repositories.reviews.update_annotation(self.first_id, note_id, invalid, updated["state_version"])
        self.assertEqual(self.repositories.reviews.get_review(self.first_id), updated)

    def test_delete_after_approval_requires_new_approval(self):
        state = self.note()
        note_id = state["annotations"][0]["id"]
        resolved = self.first.resolve(note_id, "addressed", "Fixture", "ui", state["state_version"])
        approved = self.first.approve("Approve fixture", "ui", None, True, resolved["state_version"])
        deleted = self.repositories.reviews.delete_annotation(self.first_id, note_id, approved["state_version"])
        self.assertIsNone(deleted["approval"])
        with self.assertRaisesRegex(ReviewError, "not been approved"):
            self.first.export(self.root / "final", deleted["state_version"])

    def test_http_scoped_crud_catalog_and_revision_contract(self):
        with running_server(self.repositories, self.dist) as url:
            status, bootstrap, _ = request(url, "/api/bootstrap")
            self.assertEqual(status, 200)
            token = bootstrap["csrf_token"]
            self.assertEqual(len(request(url, "/api/graphs")[1]["graphs"]), 2)
            prefix = "/api/graphs/" + self.first_id
            status, snapshot, _ = request(url, prefix + "?revision=1")
            self.assertEqual((status, snapshot["revision"]), (200, "1"))
            body = {"target_type": "node", "target_id": self.graph["nodes"][0]["id"], "kind": "note", "text": "HTTP note", "expected_version": 2}
            status, review, _ = request(url, prefix + "/annotations", "POST", body, token)
            self.assertEqual(status, 200)
            self.assertNotIn("graph", review)
            note_id = review["annotations"][0]["id"]
            note_path = prefix + "/annotations/" + note_id
            self.assertEqual(request(url, note_path)[1]["text"], "HTTP note")
            status, review, _ = request(url, note_path, "PATCH", {"text": "Edited", "expected_version": review["state_version"]}, token)
            self.assertEqual(status, 200)
            self.assertEqual(review["annotations"][0]["text"], "Edited")
            self.assertEqual(request(url, note_path, "DELETE", {"expected_version": 2}, token)[0], 409)
            status, review, _ = request(url, note_path, "DELETE", {"expected_version": review["state_version"]}, token)
            self.assertEqual(status, 200)
            self.assertEqual(request(url, prefix + "/annotations")[1], {"annotations": []})
            self.assertEqual(request(url, note_path)[0], 404)
            self.assertEqual(request(url, note_path + "?include_deleted=true")[1]["status"], "deleted")
            self.assertEqual(request(url, "/api/graphs/" + self.second_id + "/review")[1]["state_version"], 2)

    def test_http_preserves_origin_csrf_and_body_guards(self):
        with running_server(self.repositories, self.dist) as url:
            token = request(url, "/api/bootstrap")[1]["csrf_token"]
            path = "/api/graphs/" + self.first_id + "/annotations"
            body = {"target_type": "graph", "target_id": None, "kind": "note", "text": "Guarded", "expected_version": 2}
            self.assertEqual(request(url, path, "POST", body, token=None)[0], 403)
            self.assertEqual(request(url, path, "POST", body, token=token, origin=False)[0], 403)
            self.assertEqual(request(url, path, "POST", body, token=token, extra_headers={"Origin": "https://untrusted.example"})[0], 403)
            self.assertEqual(request(url, path, "POST", {**body, "approval": {}}, token)[0], 400)
            self.assertEqual(request(url, path + "?graph=other", "POST", body, token)[0], 400)
            self.assertEqual(self.first.state()["state_version"], 2)

    def test_static_assets_are_manifest_whitelisted_and_script_csp_has_no_inline(self):
        (self.dist / "secret.txt").write_text("Not in build")
        with running_server(self.repositories, self.dist) as url:
            status, html, headers = request(url, "/")
            self.assertEqual(status, 200)
            self.assertIn(b"app-123.js", html)
            self.assertIn("script-src 'self';", headers["Content-Security-Policy"])
            self.assertEqual(request(url, "/app-123.js")[2]["Content-Type"], "text/javascript; charset=utf-8")
            for path in ("/secret.txt", "/../secret.txt", "/%2e%2e/secret.txt", "/scripts/graph_model.py", "/manifest.json"):
                self.assertEqual(request(url, path)[0], 404)
        (self.dist / "app-123.js").write_text("Tampered")
        with self.assertRaisesRegex(ReviewError, "build manifest"):
            load_dist(self.dist)

    def test_missing_dist_fails_without_generating_files(self):
        missing = self.root / "missing"
        with self.assertRaisesRegex(ReviewError, "runtime does not build"):
            make_server(self.repositories, dist=missing)
        self.assertFalse(missing.exists())

    def test_packaged_dist_is_complete_and_served_without_building(self):
        packaged = SKILL / "assets" / "review-app" / "dist"
        manifest = json.loads((packaged / "manifest.json").read_text())
        before = {p.name: p.read_bytes() for p in packaged.iterdir() if p.is_file()}
        with running_server(self.repositories, packaged) as url:
            self.assertEqual(request(url, "/")[0], 200)
            for filename, expected in manifest["files"].items():
                status, body, _ = request(url, "/" + filename)
                self.assertEqual(status, 200)
                if filename == "config.json":
                    # Runtime configuration is a deliberate dynamic route; the
                    # packaged standalone default is still manifest-verified.
                    self.assertEqual(body, json.loads(before[filename]))
                    self.assertEqual(hashlib.sha256(before[filename]).hexdigest(), expected)
                else:
                    self.assertEqual(hashlib.sha256(body).hexdigest(), expected)
        self.assertEqual({p.name: p.read_bytes() for p in packaged.iterdir() if p.is_file()}, before)

    def test_legacy_single_session_api_is_compatible(self):
        with running_server(self.first, self.dist) as url:
            status, state, _ = request(url, "/api/state")
            self.assertEqual(status, 200)
            self.assertEqual(state["graph"], self.graph)
            self.assertEqual(request(url, "/api/health")[1]["session_id"], self.first_id)
            self.assertEqual(request(url, "/api/round?round=1")[1]["graph"], self.graph)
            status, updated, _ = request(url, "/api/annotations", "POST", {"target_type": "graph", "target_id": None, "kind": "note", "text": "Legacy", "expected_version": 2}, state["csrf_token"])
            self.assertEqual(status, 200)
            self.assertIn("graph", updated)
            self.assertEqual(updated["annotations"][0]["text"], "Legacy")

    def test_custom_non_sqlite_ports_work_with_opaque_ids_and_revisions(self):
        graphs = MemoryGraphs(self.graph)
        reviews = MemoryReviews(graphs.snapshot)
        pair = Repositories(graphs, reviews)
        with patch("sqlite3.connect", side_effect=AssertionError("Custom adapters must not access SQLite")):
            with running_server(pair, self.dist) as url:
                token = request(url, "/api/bootstrap")[1]["csrf_token"]
                prefix = "/api/graphs/" + quote(graphs.snapshot["id"], safe="")
                self.assertEqual(request(url, prefix + "?revision=" + quote(graphs.snapshot["revision"], safe=""))[1], graphs.snapshot)
                remote_review = request(url, prefix + "/review")[1]
                revision = remote_review["rounds"][0]["graph_revision"]
                self.assertNotEqual(revision, str(remote_review["rounds"][0]["round"]))
                status, historical, _ = request(url, prefix + "?revision=" + quote(revision, safe=""))
                self.assertEqual((status, historical["digest"]), (200, remote_review["rounds"][0]["digest"]))
                status, review, _ = request(url, prefix + "/annotations", "POST", {"target_type": "graph", "target_id": None, "kind": "note", "text": "Remote", "expected_version": 1}, token)
                self.assertEqual(status, 200)
                note_path = prefix + "/annotations/remote-note"
                status, review, _ = request(url, note_path, "PATCH", {"text": "Remote edit", "expected_version": 2}, token)
                self.assertEqual((status, review["annotations"][0]["text"]), (200, "Remote edit"))
                status, review, _ = request(url, note_path, "DELETE", {"expected_version": 3}, token)
                self.assertEqual((status, review["annotations"][0]["status"]), (200, "deleted"))

    def test_factory_loads_config_and_checks_ports(self):
        adapter = self.root / "adapter.py"
        adapter.write_text("from graph_repositories import local_repositories\ndef create(config):\n    return local_repositories(workspace=config['workspace'])\n")
        config = self.root / "config.json"
        config.write_text(json.dumps({"workspace": str(self.workspace)}))
        pair = load_repositories(str(adapter) + ":create", config)
        self.assertEqual(len(pair.graphs.list_graphs()), 2)
        adapter.write_text("def invalid(config):\n    return {'graphs': object(), 'reviews': object()}\n")
        with self.assertRaisesRegex(ReviewError, "missing callable"):
            load_repositories(str(adapter) + ":invalid", config)
        config.write_text("[]")
        with self.assertRaisesRegex(ReviewError, "JSON object"):
            load_repositories(str(adapter) + ":invalid", config)

    def test_custom_adapter_failure_is_sanitized_and_server_remains_available(self):
        graphs = MemoryGraphs(self.graph)
        reviews = MemoryReviews(graphs.snapshot)
        pair = Repositories(graphs, reviews)
        with running_server(pair, self.dist) as url:
            with patch.object(reviews, "get_review", side_effect=RuntimeError("private SDK response and credential")):
                status, body, _ = request(url, "/api/graphs/" + quote(graphs.snapshot["id"], safe="") + "/review")
                self.assertEqual(status, 500)
                self.assertNotIn("credential", json.dumps(body))
            self.assertEqual(request(url, "/api/health")[0], 200)

    def test_cli_existing_session_and_new_annotation_commands(self):
        script = str(SKILL / "scripts" / "graph_review.py")
        def cli(*args):
            result = subprocess.run([sys.executable, script, *args], text=True, capture_output=True, check=True)
            return json.loads(result.stdout)
        state = cli("status", "--session", str(self.first.directory))
        self.assertEqual(state["session_id"], self.first_id)
        state = cli("annotate", "--session", str(self.first.directory), "--expect-version", "2", "--target-type", "graph", "--kind", "note", "--text", "CLI", "--source", "agent")
        note_id = state["annotations"][0]["id"]
        state = cli("edit-annotation", "--session", str(self.first.directory), "--expect-version", str(state["state_version"]), "--id", note_id, "--text", "CLI edit", "--source", "agent")
        self.assertEqual(state["annotations"][0]["text"], "CLI edit")
        state = cli("delete-annotation", "--session", str(self.first.directory), "--expect-version", str(state["state_version"]), "--id", note_id, "--source", "agent")
        self.assertEqual(state["annotations"][0]["status"], "deleted")
        self.assertEqual(cli("validate", "--graph", str(SKILL / "assets" / "example-graph.json"))["valid"], True)


if __name__ == "__main__":
    unittest.main()
