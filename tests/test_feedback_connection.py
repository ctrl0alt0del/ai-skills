"""Shared connection, actual CLI, and app-to-agent feedback integration tests."""

from contextlib import redirect_stderr, redirect_stdout
from copy import deepcopy
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
from urllib.parse import quote

SKILL = Path(os.environ.get("BEHAVIOR_SKILL_DIR", Path(__file__).resolve().parents[1] / "skills" / "behavior-knowledge-graph-builder"))
sys.path.insert(0, str(SKILL / "scripts"))
sys.dont_write_bytecode = True
from graph_connection import read_connection, write_connection
from graph_model import Store, ReviewError, digest
from graph_review import main
from test_graph_repositories import running_server, request


ADAPTER = '''
from copy import deepcopy
import json
from pathlib import Path
from graph_model import ReviewError
from graph_repositories import Repositories

class Graphs:
    def __init__(self, config): self.config = config
    def get_graph(self, graph_id, revision=None):
        value = json.loads(Path(self.config["state_path"]).read_text())["graph"]
        if graph_id != value["id"] or revision not in (None, value["revision"]):
            raise ReviewError("Missing graph", 404)
        if self.config.get("mismatch"): value["digest"] = "wrong-digest"
        return value
    def list_graphs(self):
        value = json.loads(Path(self.config["state_path"]).read_text())["graph"]
        return [{k: v for k, v in value.items() if k != "graph"}]

class Reviews:
    def __init__(self, config): self.config = config
    def get_review(self, graph_id):
        if self.config.get("read_error"): raise OSError(self.config["secret"])
        value = json.loads(Path(self.config["state_path"]).read_text())["review"]
        if graph_id != value["graph_id"]: raise ReviewError("Missing review", 404)
        return value
    def list_annotations(self, graph_id, include_deleted=False):
        return [x for x in self.get_review(graph_id)["annotations"] if include_deleted or x["status"] != "deleted"]
    def get_annotation(self, graph_id, annotation_id, include_deleted=False):
        return next(x for x in self.list_annotations(graph_id, include_deleted) if x["id"] == annotation_id)
    def unsupported(self, *args, **kwargs): raise ReviewError("Fixture only supports processing", 405)
    create_annotation = update_annotation = delete_annotation = resolve_annotation = unsupported
    reopen_annotation = request_review = approve_review = unsupported

class ProcessingReviews(Reviews):
    def list_pending_feedback(self, graph_id):
        review = self.get_review(graph_id)
        result = {key: review[key] for key in ("graph_id", "graph_revision", "graph_digest", "state_version", "review_requested")}
        result.update(schema_version="behavior-review-feedback/1", annotations=[x for x in review["annotations"] if x["processed_revision"] < x["feedback_revision"]])
        return result
    def mark_feedback_processed(self, graph_id, annotation_id, feedback_revision, outcome, summary, processor, expected_version):
        review = self.get_review(graph_id)
        if review["state_version"] != expected_version: raise ReviewError("Stale review", 409)
        note = next(x for x in review["annotations"] if x["id"] == annotation_id)
        if note["feedback_revision"] != feedback_revision: raise ReviewError("Stale note", 409)
        note.update(processed_revision=feedback_revision, processing_status="processed",
                    processing_receipt={"feedback_revision": feedback_revision, "outcome": outcome,
                      "summary": summary, "processor": processor, "processed_at": "fixture-time",
                      "graph_revision": review["graph_revision"], "graph_digest": review["graph_digest"]})
        review["state_version"] += 1
        path = Path(self.config["state_path"])
        value = json.loads(path.read_text())
        value["review"] = review
        path.write_text(json.dumps(value))
        return deepcopy(review)

def repositories(config):
    reviews = Reviews(config) if config.get("legacy") else ProcessingReviews(config)
    return Repositories(Graphs(config), reviews)
'''


class FeedbackConnectionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.graph = json.loads((SKILL / "assets/example-graph.json").read_text())

    def tearDown(self):
        self.temp.cleanup()

    def cli(self, *args, expected=0):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = main([str(value) for value in args])
        self.assertEqual(code, expected, err.getvalue() or out.getvalue())
        return json.loads(out.getvalue() if code == 0 else err.getvalue())

    def local(self, name="review"):
        session = self.root / name
        created = self.cli("init", "--session", session, "--title", name)
        store = Store(session)
        state = store.propose(self.graph, "Fixture proposal", created["state_version"])
        state = store.annotate("graph", None, "question", "What happens after cancellation?", "ui", None, state["state_version"])
        return store, Path(created["connection"]), state

    def remote(self, **options):
        folder = self.root / "custom"
        folder.mkdir()
        adapter = folder / "adapter.py"
        adapter.write_text(ADAPTER)
        graph_id = "remote:booking/one"
        snapshot = {"id": graph_id, "title": "Remote", "revision": "etag-v3/xyz", "digest": digest(self.graph), "graph": self.graph}
        note = {"id": "remote-note", "source": "ui", "status": "open", "text": "Check cancellation",
                "feedback_revision": 2, "processed_revision": 1, "processing_status": "unprocessed", "processing_receipt": None}
        review = {"graph_id": graph_id, "graph_revision": snapshot["revision"], "graph_digest": snapshot["digest"],
                  "state_version": 8, "review_requested": True, "annotations": [note]}
        state_path = folder / "external-state.json"
        state_path.write_text(json.dumps({"graph": snapshot, "review": review}))
        config = folder / "config.json"
        config.write_text(json.dumps({"state_path": str(state_path), "secret": "private-adapter-secret", **options}))
        connection = folder / "locator" / "connection.json"
        self.cli("connect", "--connection", connection, "--repository-factory", str(adapter) + ":repositories",
                 "--repository-config", config, "--graph-id", graph_id)
        return connection, graph_id

    def test_init_locator_moves_with_session_and_cli_reloads_receipt(self):
        store, connection, state = self.local()
        descriptor = json.loads(connection.read_text())
        self.assertEqual(descriptor["repository"], {"kind": "session", "path": "."})
        moved = self.root / "moved"
        shutil.move(store.directory, moved)
        connection = moved / "connection.json"
        pending = self.cli("pending-feedback", "--connection", connection)
        note = pending["annotations"][0]
        marked = self.cli("mark-processed", "--connection", connection, "--id", note["id"],
                          "--feedback-revision", note["feedback_revision"], "--outcome", "needs_clarification",
                          "--summary", "Asked the owner to decide cancellation policy.", "--processor", "agent/test",
                          "--expect-version", pending["state_version"])
        self.assertEqual(marked["annotations"][0]["status"], "open")
        self.assertEqual(self.cli("pending-feedback", "--connection", connection)["annotations"], [])
        self.assertEqual(Store(moved).state()["annotations"][0]["processing_receipt"]["processor"], "agent/test")

    def test_workspace_requires_explicit_graph_selection_and_preserves_isolation(self):
        first, _, one = self.local("one")
        _, _, two = self.local("two")
        connection = self.root / "catalog.json"
        self.cli("connect", "--connection", connection, "--workspace", self.root)
        graphs = self.cli("feedback-graphs", "--connection", connection)["graphs"]
        self.assertEqual({x["id"] for x in graphs}, {one["session_id"], two["session_id"]})
        error = self.cli("pending-feedback", "--connection", connection, expected=1)
        self.assertIn("Select a graph", error["error"])
        pending = self.cli("pending-feedback", "--connection", connection, "--graph-id", one["session_id"])
        self.assertEqual(pending["graph_id"], first.state()["session_id"])
        self.assertEqual(len(pending["annotations"]), 1)

    def test_browser_save_cli_ack_and_browser_edit_share_same_store(self):
        store, connection, state = self.local()
        with running_server(read_connection(connection).repositories, SKILL / "assets/review-app/dist") as url:
            token = request(url, "/api/bootstrap")[1]["csrf_token"]
            path = "/api/graphs/" + quote(state["session_id"], safe="")
            code, saved, _ = request(url, path + "/annotations", "POST", {
                "expected_version": state["state_version"], "target_type": "graph", "target_id": None,
                "kind": "correction", "text": "Cancellation keeps its history."}, token)
            self.assertEqual(code, 200)
            note = saved["annotations"][-1]
            pending = self.cli("pending-feedback", "--connection", connection)
            self.assertIn(note["id"], [x["id"] for x in pending["annotations"]])
            marked = self.cli("mark-processed", "--connection", connection, "--id", note["id"],
                             "--feedback-revision", 1, "--outcome", "no_change", "--summary", "Existing claim covers history.",
                             "--processor", "agent/integration", "--expect-version", pending["state_version"])
            review = request(url, path + "/review")[1]
            self.assertEqual(review["annotations"][-1]["processing_status"], "processed")
            code, edited, _ = request(url, path + "/annotations/" + note["id"], "PATCH", {
                "expected_version": marked["state_version"], "text": "Keep history for failed cancellations too."}, token)
            self.assertEqual(code, 200)
            pending = self.cli("pending-feedback", "--connection", connection)
            current = next(x for x in pending["annotations"] if x["id"] == note["id"])
            self.assertEqual(current["feedback_revision"], 2)
            self.assertEqual(current["processing_receipt"]["feedback_revision"], 1)
            error = self.cli("mark-processed", "--connection", connection, "--id", note["id"],
                             "--feedback-revision", 1, "--outcome", "no_change", "--summary", "Old work", "--processor", "agent/integration",
                             "--expect-version", edited["state_version"], expected=1)
            self.assertIn("Feedback changed", error["error"])
            self.assertEqual(store.state()["graph_digest"], state["graph_digest"])

    def test_custom_adapter_uses_opaque_revisions_and_persists_without_sqlite(self):
        connection, graph_id = self.remote()
        descriptor = connection.read_text()
        self.assertNotIn("private-adapter-secret", descriptor)
        self.assertIn("../adapter.py:repositories", descriptor)
        with patch("sqlite3.connect", side_effect=AssertionError("Custom adapter must not use SQLite")):
            status = self.cli("feedback-status", "--connection", connection)
            self.assertEqual(status["graph"]["revision"], "etag-v3/xyz")
            pending = self.cli("pending-feedback", "--connection", connection)
            self.assertEqual(pending["graph_id"], graph_id)
            self.cli("mark-processed", "--connection", connection, "--id", "remote-note", "--feedback-revision", 2,
                     "--outcome", "needs_clarification", "--summary", "Recorded question for the policy owner.",
                     "--processor", "agent/remote", "--expect-version", 8)
            self.assertEqual(self.cli("pending-feedback", "--connection", connection)["annotations"], [])
        completed = subprocess.run([sys.executable, str(SKILL / "scripts/graph_review.py"), "feedback-status", "--connection", str(connection)],
                                   cwd=self.root, text=True, capture_output=True, check=True)
        self.assertEqual(json.loads(completed.stdout)["review"]["annotations"][0]["processed_revision"], 2)

    def test_old_adapter_can_read_but_cannot_silently_acknowledge(self):
        connection, _ = self.remote(legacy=True)
        self.cli("feedback-status", "--connection", connection)
        error = self.cli("pending-feedback", "--connection", connection, expected=1)
        self.assertIn("does not support durable feedback processing", error["error"])

    def test_connection_reuse_refuses_different_settings_and_symlink(self):
        store, connection, state = self.local()
        before = connection.read_bytes()
        self.cli("connect", "--connection", connection, "--session", store.directory)
        self.assertEqual(connection.read_bytes(), before)
        other, _, _ = self.local("other")
        self.cli("connect", "--connection", connection, "--session", other.directory, expected=1)
        self.assertEqual(connection.read_bytes(), before)
        symlink = self.root / "link.json"
        symlink.symlink_to(connection)
        with self.assertRaises(ReviewError):
            write_connection(symlink, session=store.directory)
        self.assertEqual(connection.read_bytes(), before)

    def test_connection_rejects_inline_secrets_unknown_schema_and_unknown_graph(self):
        store, connection, _ = self.local()
        value = json.loads(connection.read_text())
        for update in ({"credentials": "private"}, {"schema_version": "future/999"}):
            bad = self.root / "bad.json"
            bad.write_text(json.dumps({**value, **update}))
            with self.assertRaises(ReviewError): read_connection(bad)
        with self.assertRaises(ReviewError):
            write_connection(self.root / "unknown.json", session=store.directory, graph_id="missing")
        self.assertFalse((self.root / "unknown.json").exists())

    def test_interrupted_connection_write_does_not_publish_partial_locator(self):
        store, _, _ = self.local()
        connection = self.root / "recoverable.json"
        with patch("graph_connection.os.fdopen", side_effect=OSError("Interrupted write")):
            self.cli("connect", "--connection", connection, "--session", store.directory, expected=1)
        self.assertFalse(connection.exists())
        self.assertEqual(list(self.root.glob(".review-connection-*")), [])
        self.cli("connect", "--connection", connection, "--session", store.directory)
        self.assertEqual(read_connection(connection).graph_id, store.state()["session_id"])

    def test_status_rejects_mismatched_snapshot_and_sanitizes_adapter_errors(self):
        connection, _ = self.remote(mismatch=True)
        error = self.cli("feedback-status", "--connection", connection, expected=1)
        self.assertIn("do not match", error["error"])
        config = connection.parent.parent / "config.json"
        value = json.loads(config.read_text())
        value.update(mismatch=False, read_error=True)
        config.write_text(json.dumps(value))
        error = self.cli("pending-feedback", "--connection", connection, expected=1)
        self.assertNotIn("private-adapter-secret", json.dumps(error))
        self.assertIn("processing was not confirmed", error["error"])

    def test_pending_wait_is_bounded_and_notices_a_later_review(self):
        store, connection, state = self.local()
        for timeout in ("-1", "31", "nan", "inf"):
            self.cli("pending-feedback", "--connection", connection, "--after-version", state["state_version"], "--timeout", timeout, expected=1)
        self.cli("pending-feedback", "--connection", connection, "--timeout", 1, expected=1)
        unchanged = self.cli("pending-feedback", "--connection", connection, "--after-version", state["state_version"])
        self.assertFalse(unchanged["changed"])
        failures = []
        def later():
            try:
                time.sleep(0.08)
                store.request_review(state["state_version"])
            except Exception as error: failures.append(error)
        thread = threading.Thread(target=later)
        thread.start()
        changed = self.cli("pending-feedback", "--connection", connection, "--after-version", state["state_version"], "--timeout", 2)
        thread.join(timeout=3)
        self.assertFalse(failures)
        self.assertTrue(changed["changed"])
        self.assertTrue(changed["review_requested"])
        self.assertFalse(store.state()["agent_active"])


if __name__ == "__main__":
    unittest.main()
