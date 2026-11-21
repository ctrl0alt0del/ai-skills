"""Pure builder packets, app-only continuation, and interruption-safe publication."""

from copy import deepcopy
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch


REPO = Path(__file__).resolve().parents[1]
APP = Path(os.environ.get("BEHAVIOR_REVIEW_SKILL_DIR", REPO / "skills/behavior-graph-review-app"))
BUILDER = Path(os.environ.get("BEHAVIOR_BUILDER_SKILL_DIR", REPO / "skills/behavior-knowledge-graph-builder"))
sys.path.insert(0, str(APP / "scripts"))
sys.dont_write_bytecode = True

from graph_schema import GraphError, digest
from graph_model import Store
from document_repositories import DocumentGraphRepository, DocumentHumanReviewRepository, graph_key
from repository_config import load_repository_config
from build_contract import make_request, make_result, read_packet, validate_result, write_new_packet
from review_session import prepare, apply_result, status


class BuilderHandoffTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.graph = json.loads((BUILDER / "assets/example-graph.json").read_text())
        self.store = Store(self.root / "review")
        self.store.initialize("Fictional review")
        self.graph_id = self.store.state()["session_id"]
        self.locator = {"kind": "session", "path": str(self.store.directory)}
        self.work = self.root / "handoff"

    def proposal(self):
        return self.store.propose(self.graph, "Initial proposal", 1)

    def feedback(self):
        current = self.store.state()
        return self.store.annotate("graph", None, "question", "What is the retry policy?", "ui", None,
                                   current["state_version"])

    def request(self, mode="review", **kwargs):
        return prepare(locator=self.locator, work=self.work, mode=mode, scope=["Orders"], **kwargs)

    def result(self, graph=None, *, report=None):
        request = read_packet(self.work / "request.json")
        graph = deepcopy(self.graph if graph is None else graph)
        if report is None:
            report = {"summary": "Reviewed the available evidence.", "questions": [], "feedback": []}
            for note in request["feedback"]:
                report["questions"].append("Which retry policy should apply?")
                report["feedback"].append({"annotation_id": note["id"], "feedback_revision": note["feedback_revision"],
                                           "outcome": "needs_clarification", "summary": "Policy is undecided.", "disposition": None})
        value = make_result(request, graph, report)
        path = self.root / "builder-result.json"
        write_new_packet(path, value)
        return path, value

    def assert_conflict(self, operation):
        with self.assertRaises(GraphError) as caught:
            operation()
        self.assertEqual(caught.exception.status, 409)

    def config(self):
        value = {"schema_version": "behavior-review-config/1",
                 "repositories": {"g": {"type": "filesystem", "path": "./graphs"},
                                  "r": {"type": "sqlite", "path": "./reviews.sqlite3"}},
                 "bindings": {"graphs": "g", "reviews": "r"}, "graph_id": "orders:configured/one"}
        path = self.root / "config.json"
        path.write_text(json.dumps(value))
        return path, value["graph_id"]

    def test_pure_contract_copies_and_builder_without_app_are_portable(self):
        self.assertEqual((APP / "scripts/build_contract.py").read_bytes(), (BUILDER / "scripts/build_contract.py").read_bytes())
        isolated = self.root / "builder-only"
        isolated.mkdir()
        for name in ("graph_schema.py", "build_contract.py", "graph_build.py"):
            shutil.copy2(BUILDER / "scripts" / name, isolated / name)
        graph_file = self.root / "graph.json"
        graph_file.write_text(json.dumps(self.graph))
        completed = subprocess.run([sys.executable, str(isolated / "graph_build.py"), "validate", "--graph", str(graph_file)],
                                   cwd=isolated, capture_output=True, text=True, check=False)
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertTrue(json.loads(completed.stdout)["valid"])

    def test_create_request_owns_no_storage_locator_and_app_checkpoint_retains_it(self):
        output = self.request("create", sources=[str(self.root / "source")])
        request = read_packet(self.work / "request.json")
        checkpoint = read_packet(self.work / "checkpoint.json")
        self.assertEqual(output["phase"], "prepared")
        self.assertEqual(request["base"]["round"], 0)
        self.assertIsNone(request["base_graph"])
        self.assertEqual(request["sources"], [str(self.root / "source")])
        self.assertNotIn("locator", request)
        self.assertNotIn("config", request)
        self.assertEqual(checkpoint["locator"], self.locator)
        path, _ = self.result()
        result = apply_result(self.work, path)
        self.assertEqual(result["phase"], "published")
        self.assertEqual(self.store.state()["round"], 1)
        self.assertIsNone(self.store.state()["approval"])

    def test_changed_graph_publishes_once_and_resume_reads_authoritative_state(self):
        self.proposal(); self.feedback(); self.request()
        changed = deepcopy(self.graph)
        changed["nodes"][0]["summary"] += " Updated behavior."
        path, _ = self.result(changed)
        first = apply_result(self.work, path)
        self.assertFalse(first["reused_publication"])
        self.assertEqual(first["publication"]["round"], 2)
        before = self.store.state()["state_version"]
        second = apply_result(self.work, path)
        self.assertTrue(second["reused_publication"])
        self.assertEqual(self.store.state()["round"], 2)
        self.assertEqual(self.store.state()["state_version"], before)
        self.assertEqual(status(self.work)["current"]["graph"]["digest"], digest(changed))

    def test_unchanged_result_keeps_round_and_feedback_unprocessed(self):
        self.proposal(); self.feedback(); self.request()
        original = self.store.state()
        path, _ = self.result()
        response = apply_result(self.work, path)
        current = self.store.state()
        self.assertFalse(response["publication"]["changed"])
        self.assertEqual(current["round"], original["round"])
        self.assertEqual(current["state_version"], original["state_version"])
        self.assertEqual(current["annotations"], original["annotations"])
        self.assertEqual(len(response["current"]["pending"]["annotations"]), 1)
        self.assertEqual(response["feedback_recommendations"][0]["outcome"], "needs_clarification")
        self.assertIsNone(current["approval"])

    def test_wrong_result_identity_and_forged_digest_are_rejected_without_mutation(self):
        self.proposal(); self.feedback(); self.request()
        path, value = self.result()
        before = self.store.state()
        for field, replacement in (("request_id", "another-request"), ("graph_digest", "wrong-digest"),
                                   ("base", {**value["base"], "graph_id": "different-graph"})):
            malformed = {**deepcopy(value), field: replacement}
            path.write_text(json.dumps(malformed))
            self.assert_conflict(lambda: apply_result(self.work, path))
        self.assertEqual(self.store.state(), before)
        self.assertEqual(read_packet(self.work / "checkpoint.json")["phase"], "prepared")

    def test_new_human_revision_rejects_the_prepared_result(self):
        self.proposal(); self.feedback(); self.request()
        path, _ = self.result()
        state = self.store.state()
        self.store.edit_annotation(state["annotations"][0]["id"], {"text": "Changed question"}, "ui", state["state_version"])
        self.assert_conflict(lambda: apply_result(self.work, path))
        self.assertFalse(status(self.work)["base_current"])
        self.assertEqual(self.store.state()["round"], 1)

    def test_result_must_account_for_exact_selected_feedback_revisions(self):
        self.proposal(); self.feedback(); self.request()
        _, value = self.result()
        request = read_packet(self.work / "request.json")
        value["feedback"][0]["feedback_revision"] += 1
        self.assert_conflict(lambda: validate_result(value, request))
        value["feedback"] = []
        with self.assertRaises(GraphError):
            validate_result(value, request)

    def test_request_cannot_be_rewritten_to_refresh_its_version(self):
        self.proposal(); self.feedback(); self.request()
        path, _ = self.result()
        request = read_packet(self.work / "request.json")
        request["base"]["state_version"] += 1
        (self.work / "request.json").write_text(json.dumps(request))
        self.assert_conflict(lambda: apply_result(self.work, path))

    def test_code_change_selection_keeps_unsubmitted_feedback_separate(self):
        self.proposal(); self.feedback()
        changes = [{"source_id": "repo", "location": str(self.root / "source"),
                    "from_revision": "old", "to_revision": "new", "paths": ["cancel.js"]}]
        self.request("code_change", source_changes=changes)
        request = read_packet(self.work / "request.json")
        self.assertEqual(request["feedback"], [])
        self.assertEqual(request["source_changes"], changes)
        changed = deepcopy(self.graph)
        changed["nodes"][0]["summary"] += " Changed from source evidence."
        _, result = self.result(changed)
        self.assertEqual(result["changes"]["nodes"]["updated"], [changed["nodes"][0]["id"]])

    def test_interruption_after_publication_blocks_duplicate_apply(self):
        self.proposal(); self.feedback(); self.request()
        changed = deepcopy(self.graph); changed["project"]["name"] += " changed"
        path, _ = self.result(changed)
        original = Store.propose
        class Interrupted(BaseException):
            pass
        def interrupted(store, *args):
            original(store, *args)
            raise Interrupted()
        with patch.object(Store, "propose", interrupted), self.assertRaises(Interrupted):
            apply_result(self.work, path)
        self.assertEqual(self.store.state()["round"], 2)
        self.assertEqual(read_packet(self.work / "checkpoint.json")["phase"], "applying")
        self.assert_conflict(lambda: apply_result(self.work, path))
        resumed = status(self.work)
        self.assertTrue(resumed["reconciliation_required"])
        self.assertEqual(resumed["current"]["review"]["round"], 2)
        self.assertEqual(self.store.state()["round"], 2)

    def test_transport_style_failure_after_commit_requires_reconciliation(self):
        self.proposal(); self.feedback(); self.request()
        changed = deepcopy(self.graph); changed["project"]["name"] += " changed"
        path, _ = self.result(changed)
        original = Store.propose
        def failed_reply(store, *args):
            original(store, *args)
            raise RuntimeError("reply interrupted after commit")
        with patch.object(Store, "propose", failed_reply), self.assertRaises(RuntimeError):
            apply_result(self.work, path)
        self.assertEqual(status(self.work)["phase"], "needs-reconciliation")
        self.assert_conflict(lambda: apply_result(self.work, path))
        self.assertEqual(self.store.state()["round"], 2)

    def test_new_configured_target_provisions_only_when_result_is_applied(self):
        config, identifier = self.config()
        prepare(locator={"kind": "config", "path": str(config)}, work=self.work, mode="create", scope=["Orders"])
        request = read_packet(self.work / "request.json")
        self.assertIsNone(request["base"])
        self.assertNotIn(identifier, json.dumps(request))
        self.assertFalse((self.root / "graphs").exists())
        self.assertFalse((self.root / "reviews.sqlite3").exists())
        path, _ = self.result()
        published = apply_result(self.work, path)
        self.assertEqual(published["publication"]["graph_id"], identifier)
        self.assertEqual(published["publication"]["graph_revision"], request["request_id"])
        self.assertEqual(published["current"]["review"]["state_version"], 1)
        self.assertTrue(apply_result(self.work, path)["reused_publication"])

    def test_partial_new_target_is_visible_and_never_blindly_reinitialized(self):
        config, identifier = self.config()
        prepare(locator={"kind": "config", "path": str(config)}, work=self.work, mode="create", scope=["Orders"])
        path, _ = self.result()
        with patch.object(DocumentHumanReviewRepository, "initialize_review", side_effect=RuntimeError("write failed")), self.assertRaises(RuntimeError):
            apply_result(self.work, path)
        resumed = status(self.work)
        self.assertEqual(resumed["phase"], "needs-reconciliation")
        self.assertTrue(resumed["current"]["partial_publication"])
        self.assertEqual(resumed["current"]["graph"]["id"], identifier)
        self.assert_conflict(lambda: apply_result(self.work, path))
        with self.assertRaises(GraphError):
            load_repository_config(config).repositories.reviews.get_review(identifier)

    def test_target_created_after_preparation_cannot_be_overwritten(self):
        config, identifier = self.config()
        prepare(locator={"kind": "config", "path": str(config)}, work=self.work, mode="create", scope=["Orders"])
        path, _ = self.result()
        repositories = load_repository_config(config).repositories
        repositories.graphs.publish_graph(identifier, "someone-elses-revision", self.graph)
        repositories.reviews.initialize_review(identifier, "someone-elses-revision")
        self.assert_conflict(lambda: apply_result(self.work, path))
        self.assertEqual(repositories.reviews.get_review(identifier)["graph_revision"], "someone-elses-revision")

    def test_resume_exposes_an_immutable_snapshot_before_catalog_publication(self):
        config, identifier = self.config()
        prepare(locator={"kind": "config", "path": str(config)}, work=self.work, mode="create", scope=["Orders"])
        path, result = self.result()
        def interrupted_catalog(repository, graph_id, revision, graph, title=None):
            repository.store.create(graph_key(graph_id, revision), {"id": graph_id, "title": graph["project"]["name"],
                                      "revision": revision, "digest": digest(graph), "graph": graph})
            raise RuntimeError("Catalog write did not complete")
        with patch.object(DocumentGraphRepository, "publish_graph", interrupted_catalog), self.assertRaises(RuntimeError):
            apply_result(self.work, path)
        resumed = status(self.work)
        self.assertFalse(resumed["current"]["exists"])
        self.assertIsNone(resumed["current"]["graph"])
        self.assertEqual(resumed["publication_candidate"]["digest"], result["graph_digest"])
        self.assert_conflict(lambda: apply_result(self.work, path))

    def test_proposal_reopening_does_not_acknowledge_the_new_feedback_revision(self):
        self.proposal()
        state = self.feedback()
        note = state["annotations"][0]
        self.store.resolve(note["id"], "addressed", "Initial answer", "agent", state["state_version"])
        self.request()
        changed = deepcopy(self.graph); changed["project"]["name"] += " changed"
        path, _ = self.result(changed)
        response = apply_result(self.work, path)
        current_note = self.store.state()["annotations"][0]
        self.assertEqual(current_note["status"], "open")
        self.assertEqual(current_note["feedback_revision"], 2)
        self.assertEqual(current_note["processed_revision"], 0)
        self.assertEqual(response["feedback_recommendations"][0]["feedback_revision"], 1)

    def test_cli_result_is_exclusive_and_does_not_touch_the_review(self):
        self.request("create")
        graph = self.root / "proposal.json"; graph.write_text(json.dumps(self.graph))
        report = self.root / "report.json"; report.write_text(json.dumps({"summary": "First draft", "questions": [], "feedback": []}))
        destination = self.root / "result.json"
        command = [sys.executable, str(BUILDER / "scripts/graph_build.py"), "result", "--request", str(self.work / "request.json"),
                   "--graph", str(graph), "--report", str(report), "--out", str(destination)]
        completed = subprocess.run(command, capture_output=True, text=True, check=False)
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(self.store.state()["round"], 0)
        before = destination.read_bytes()
        repeated = subprocess.run(command, capture_output=True, text=True, check=False)
        self.assertNotEqual(repeated.returncode, 0)
        self.assertEqual(destination.read_bytes(), before)


if __name__ == "__main__":
    unittest.main()
