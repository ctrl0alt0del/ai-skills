"""Durable feedback inbox, revision races, and approval compatibility."""

from copy import deepcopy
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest


SKILL = Path(os.environ.get("BEHAVIOR_SKILL_DIR", Path(__file__).resolve().parents[1] / "skills" / "behavior-knowledge-graph-builder"))
sys.path.insert(0, str(SKILL / "scripts"))
sys.dont_write_bytecode = True
from graph_model import Store, ReviewError, PROCESSING_FIELDS, canonical, digest, review_digest
from graph_repositories import check_feedback_repository, local_repositories


class FeedbackProcessingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.store = Store(self.root / "session")
        self.store.initialize("Feedback fixture")
        self.graph = json.loads((SKILL / "assets" / "example-graph.json").read_text())
        self.store.propose(self.graph, "Initial fixture", 1)
        self.repositories = local_repositories(session=self.store)
        self.reviews = self.repositories.reviews
        self.graph_id = self.store.state()["session_id"]

    def version(self):
        return self.store.state()["state_version"]

    def note(self, source="ui", target_type="graph", target_id=None):
        state = self.store.annotate(target_type, target_id, "question", "What happens after a retry?",
                                    source, "conversation:test" if source == "chat" else None,
                                    self.version())
        return state["annotations"][-1]

    def acknowledge(self, note, outcome="addressed", summary="Recorded the inspected result.", **changes):
        arguments = dict(annotation_id=note["id"], feedback_revision=note["feedback_revision"],
                         outcome=outcome, summary=summary, processor="test-agent", expected_version=self.version())
        arguments.update(changes)
        return self.store.mark_feedback_processed(**arguments)

    def current_note(self, note):
        return next(item for item in self.store.state()["annotations"] if item["id"] == note["id"])

    def test_human_feedback_pending_agent_notes_do_not_feed_back_to_agent(self):
        ui, chat, agent = self.note("ui"), self.note("chat"), self.note("agent")
        inbox = self.reviews.list_pending_feedback(self.graph_id)
        self.assertEqual(inbox["schema_version"], "behavior-review-feedback/1")
        self.assertEqual(inbox["graph_id"], self.graph_id)
        self.assertEqual(inbox["graph_revision"], "1")
        self.assertEqual(inbox["graph_digest"], digest(self.graph))
        self.assertEqual(inbox["state_version"], self.version())
        self.assertFalse(inbox["review_requested"])
        self.assertEqual({n["id"] for n in inbox["annotations"]}, {ui["id"], chat["id"]})
        self.assertEqual(agent["feedback_revision"], 0)
        self.assertEqual(agent["processing_status"], "processed")
        self.assertIsNone(agent["processing_receipt"])

    def test_processing_can_complete_while_question_remains_open_and_submitted(self):
        note = self.note()
        self.store.request_review(self.version())
        state = self.acknowledge(note, "needs_clarification", "Need the owner to choose the retry policy.")
        result = state["annotations"][0]
        self.assertEqual(result["status"], "open")
        self.assertIsNone(result["resolution"])
        self.assertEqual(result["processed_revision"], 1)
        self.assertEqual(result["processing_status"], "processed")
        self.assertTrue(state["review_requested"])
        self.assertIsNone(state["approval"])
        self.assertEqual(self.store.list_pending_feedback()["annotations"], [])
        with self.assertRaisesRegex(ReviewError, "open annotations"):
            self.store.approve("Approved", "ui", None, True, self.version())

    def test_edit_while_processing_rejects_stale_state_then_stale_note_revision(self):
        note = self.note()
        old_version = self.version()
        self.store.edit_annotation(note["id"], {"text": "Revised: retry twice, not once."}, "ui", old_version)
        for version in (old_version, self.version()):
            with self.subTest(version=version), self.assertRaises(ReviewError) as caught:
                self.acknowledge(note, expected_version=version)
            self.assertEqual(caught.exception.status, 409)
        current = self.current_note(note)
        self.assertEqual(current["feedback_revision"], 2)
        self.assertEqual(current["processed_revision"], 0)
        self.assertEqual(self.store.list_pending_feedback()["annotations"], [current])

    def test_edit_after_processing_requeues_and_preserves_previous_receipt(self):
        note = self.note()
        old = self.acknowledge(note)["annotations"][0]
        self.store.edit_annotation(note["id"], {"kind": "correction"}, "chat", self.version())
        new = self.current_note(note)
        self.assertEqual((new["feedback_revision"], new["processed_revision"]), (2, 1))
        self.assertEqual(new["processing_status"], "unprocessed")
        self.assertEqual(new["processing_receipt"], old["processing_receipt"])
        self.acknowledge(new)
        self.assertEqual(self.current_note(note)["processing_receipt"]["feedback_revision"], 2)

    def test_withdrawal_remains_discoverable_until_exact_tombstone_is_processed(self):
        note = self.note()
        self.acknowledge(note)
        self.store.delete_annotation(note["id"], "ui", self.version())
        self.assertEqual(self.reviews.list_annotations(self.graph_id), [])
        pending = self.reviews.list_pending_feedback(self.graph_id)["annotations"]
        self.assertEqual(len(pending), 1)
        withdrawn = pending[0]
        self.assertEqual(withdrawn["id"], note["id"])
        self.assertEqual(withdrawn["status"], "deleted")
        self.assertEqual(withdrawn["text"], note["text"])
        self.assertEqual(withdrawn["feedback_revision"], 2)
        with self.assertRaises(ReviewError):
            self.acknowledge(note, "withdrawn")
        self.acknowledge(withdrawn, "withdrawn", "No implementation used the withdrawn request.")
        self.assertEqual(self.store.list_pending_feedback()["annotations"], [])
        self.assertEqual(self.current_note(note)["status"], "deleted")

    def test_human_resolution_requeues_but_agent_resolution_does_not(self):
        note = self.note()
        self.acknowledge(note)
        state = self.store.resolve(note["id"], "addressed", "Existing evidence answers this.", "agent", self.version())
        self.assertEqual(state["annotations"][0]["feedback_revision"], 1)
        self.assertEqual(state["annotations"][0]["processing_status"], "processed")
        state = self.store.reopen(note["id"], self.version(), "ui")
        self.assertEqual(state["annotations"][0]["feedback_revision"], 2)
        self.acknowledge(state["annotations"][0], "needs_clarification")
        state = self.store.resolve(note["id"], "accepted_gap", "I accept the documented uncertainty.", "ui", self.version())
        self.assertEqual(state["annotations"][0]["feedback_revision"], 3)
        self.assertEqual(state["annotations"][0]["processing_status"], "unprocessed")

    def test_pending_survives_new_proposal_and_cleared_submission_flag(self):
        note = self.note()
        self.store.request_review(self.version())
        self.store.propose(self.graph, "Same contents, another presentation", self.version())
        inbox = self.store.list_pending_feedback()
        self.assertFalse(inbox["review_requested"])
        self.assertEqual(inbox["graph_revision"], "2")
        self.assertEqual(inbox["annotations"][0]["id"], note["id"])
        self.assertEqual(inbox["annotations"][0]["feedback_revision"], 1)

    def test_proposal_target_change_requeues_settled_processed_feedback(self):
        node = self.graph["nodes"][0]
        note = self.note(target_type="node", target_id=node["id"])
        self.store.resolve(note["id"], "addressed", "Verified current behavior.", "agent", self.version())
        self.acknowledge(note)
        altered = deepcopy(self.graph)
        altered["nodes"][0]["summary"] += " A new behavior detail."
        self.store.propose(altered, "Revised target", self.version())
        reopened = self.current_note(note)
        self.assertEqual(reopened["status"], "open")
        self.assertEqual((reopened["feedback_revision"], reopened["processed_revision"]), (2, 1))
        with self.assertRaises(ReviewError):
            self.acknowledge(note)

    def test_failed_completion_and_new_arrivals_cannot_consume_unseen_feedback(self):
        original = self.note()
        inspected_version = self.version()
        arrived = self.note("chat")
        with self.assertRaises(ReviewError):
            self.acknowledge(original, expected_version=inspected_version)
        self.assertEqual(len(self.store.list_pending_feedback()["annotations"]), 2)
        self.acknowledge(original)
        pending = self.store.list_pending_feedback()["annotations"]
        self.assertEqual([n["id"] for n in pending], [arrived["id"]])

    def test_receipts_survive_restart_and_duplicate_acknowledgment_is_visible(self):
        note = self.note("chat")
        state = self.acknowledge(note)
        self.store = Store(self.store.directory)
        self.assertEqual(self.store.state()["annotations"], state["annotations"])
        receipt = self.current_note(note)["processing_receipt"]
        self.assertEqual(receipt["processor"], "test-agent")
        self.assertEqual(receipt["graph_revision"], "1")
        self.assertEqual(receipt["graph_digest"], digest(self.graph))
        self.assertTrue(receipt["processed_at"].endswith("Z"))
        before = self.version()
        with self.assertRaisesRegex(ReviewError, "already processed") as caught:
            self.acknowledge(note)
        self.assertEqual(caught.exception.status, 409)
        self.assertEqual(self.version(), before)
        with self.store.connection() as connection:
            events = [json.loads(row[0]) for row in connection.execute("SELECT content FROM audit")]
        processed = [event for event in events if event.get("action") == "feedback_processed"]
        self.assertEqual(len(processed), 1)
        self.assertEqual(processed[0]["payload"]["receipt"], receipt)

    def test_processing_does_not_change_semantic_approval_or_export(self):
        note = self.note()
        self.store.resolve(note["id"], "addressed", "Behavior already covers it.", "agent", self.version())
        before = self.store.approve("Approved exact fixture", "ui", None, True, self.version())
        after = self.acknowledge(note, "no_change")
        self.assertEqual(after["approval"], before["approval"])
        self.assertEqual(review_digest(after), review_digest(before))
        exported = self.store.export(self.root / "output", self.version())
        self.assertIsNotNone(exported["finalized"])
        review = json.loads((self.root / "output" / "review.json").read_text())
        self.assertEqual(review["annotations"][0]["processing_status"], "processed")
        self.assertEqual(review["audit"][-1]["action"], "feedback_processed")

    def test_legacy_notes_migrate_on_read_without_write_or_approval_invalidation(self):
        note = self.note()
        self.store.resolve(note["id"], "addressed", "Resolved before upgrade.", "agent", self.version())
        self.store.approve("Legacy approval", "ui", None, True, self.version())
        with self.store.connection() as connection:
            old = json.loads(connection.execute("SELECT content FROM state WHERE id=1").fetchone()[0])
            for entry in old["annotations"]:
                for key in PROCESSING_FIELDS:
                    entry.pop(key, None)
            old["approval"]["review_digest"] = digest({key: old[key] for key in ("round", "graph_digest", "annotations", "review_requested")})
            raw = canonical(old)
            connection.execute("UPDATE state SET content=? WHERE id=1", (raw,))
        migrated = self.store.state()
        self.assertEqual(migrated["approval"], old["approval"])
        self.assertEqual(review_digest(migrated), old["approval"]["review_digest"])
        self.assertEqual(migrated["annotations"][0]["processing_status"], "unprocessed")
        with self.store.connection() as connection:
            self.assertEqual(connection.execute("SELECT content FROM state WHERE id=1").fetchone()[0], raw)
        # An existing legacy approval remains usable; operational migration cannot
        # silently add another approval gate to the already-approved snapshot.
        self.store.export(self.root / "legacy-output", self.version())
        after = self.acknowledge(migrated["annotations"][0], "no_change")
        self.assertIsNotNone(after["finalized"])
        self.assertEqual(after["approval"], old["approval"])

    def test_legacy_agent_notes_with_human_touch_are_pending(self):
        untouched = self.note("agent")
        human_resolved = self.note("agent")
        self.store.resolve(human_resolved["id"], "addressed", "Human answered.", "ui", self.version())
        with self.store.connection() as connection:
            old = json.loads(connection.execute("SELECT content FROM state WHERE id=1").fetchone()[0])
            for note in old["annotations"]:
                for key in PROCESSING_FIELDS:
                    note.pop(key, None)
            connection.execute("UPDATE state SET content=? WHERE id=1", (canonical(old),))
        self.assertEqual(self.current_note(untouched)["feedback_revision"], 0)
        self.assertEqual(self.store.list_pending_feedback()["annotations"][0]["id"], human_resolved["id"])

    def test_legacy_reopens_are_recovered_from_audit_without_writing(self):
        human_reopened = self.note("agent")
        auto_reopened = self.note("agent")
        unchanged = self.note("agent")
        for note in (human_reopened, auto_reopened):
            self.store.resolve(note["id"], "addressed", "Previously discussed.", "agent", self.version())
        self.store.reopen(human_reopened["id"], self.version(), "ui")
        changed = deepcopy(self.graph)
        changed["nodes"][0]["summary"] += " Changed after discussion."
        self.store.propose(changed, "Changed graph", self.version())
        with self.store.connection() as connection:
            old = json.loads(connection.execute("SELECT content FROM state WHERE id=1").fetchone()[0])
            for entry in old["annotations"]:
                for key in PROCESSING_FIELDS:
                    entry.pop(key, None)
            raw = canonical(old)
            connection.execute("UPDATE state SET content=? WHERE id=1", (raw,))
            old_audit_count = connection.execute("SELECT COUNT(*) FROM audit").fetchone()[0]
        inbox = self.store.list_pending_feedback()
        self.assertEqual({note["id"] for note in inbox["annotations"]},
                         {human_reopened["id"], auto_reopened["id"]})
        self.assertTrue(all(note["feedback_revision"] == 1 for note in inbox["annotations"]))
        self.assertEqual(self.current_note(unchanged)["feedback_revision"], 0)
        with self.store.connection() as connection:
            self.assertEqual(connection.execute("SELECT content FROM state WHERE id=1").fetchone()[0], raw)
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM audit").fetchone()[0], old_audit_count)

    def test_current_receipts_are_not_requeued_by_historical_audit(self):
        note = self.note("agent")
        self.store.resolve(note["id"], "addressed", "Previously discussed.", "agent", self.version())
        state = self.store.reopen(note["id"], self.version(), "chat")
        current = state["annotations"][0]
        self.acknowledge(current, "needs_clarification")
        self.assertEqual(self.store.list_pending_feedback()["annotations"], [])
        self.assertEqual(self.current_note(note)["processed_revision"], current["feedback_revision"])

    def test_invalid_receipts_do_not_modify_state(self):
        note = self.note()
        before = self.store.state()
        for arguments in ({"feedback_revision": True}, {"feedback_revision": 0}, {"feedback_revision": "1"},
                          {"outcome": "success"}, {"summary": "   "}, {"processor": ""}):
            with self.subTest(arguments=arguments), self.assertRaises(ReviewError):
                self.acknowledge(note, **arguments)
        self.assertEqual(self.store.state(), before)

    def test_optional_adapter_capability_and_graph_scoping(self):
        self.assertIs(check_feedback_repository(self.reviews), self.reviews)
        with self.assertRaises(ReviewError) as unsupported:
            check_feedback_repository(object())
        self.assertEqual(unsupported.exception.status, 501)
        note = self.note()
        with self.assertRaises(ReviewError) as missing:
            self.reviews.mark_feedback_processed("other-graph", note["id"], 1,
                                                  "addressed", "Read it", "test-agent", self.version())
        self.assertEqual(missing.exception.status, 404)
        result = self.reviews.mark_feedback_processed(self.graph_id, note["id"], 1,
                                                      "needs_clarification", "Await owner", "test-agent", self.version())
        self.assertNotIn("graph", result)
        self.assertEqual(result["graph_id"], self.graph_id)
        self.assertEqual(result["annotations"][0]["processing_status"], "processed")


if __name__ == "__main__":
    unittest.main()
