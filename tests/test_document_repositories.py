"""Persistence, mixed bindings, and shared review-domain adapter regressions."""

from copy import deepcopy
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch


SKILL = Path(os.environ.get("BEHAVIOR_SKILL_DIR", Path(__file__).resolve().parents[1] / "skills" / "behavior-knowledge-graph-builder"))
sys.path.insert(0, str(SKILL / "scripts"))
sys.dont_write_bytecode = True

from graph_model import Store, ReviewError, digest, review_digest
from document_repositories import (CATALOG_KEY, DocumentGraphRepository,
                                   DocumentHumanReviewRepository, graph_key, review_key)
from filesystem_repository import FilesystemDocumentStore
from sqlite_repository import SQLiteDocumentStore


class StorageContractTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def stores(self, kind, name):
        if kind == "filesystem":
            path = self.root / name
            return FilesystemDocumentStore(path), FilesystemDocumentStore(path), path
        path = self.root / name / "documents.sqlite3"
        return SQLiteDocumentStore(path), SQLiteDocumentStore(path), path

    def assert_status(self, status, operation):
        with self.assertRaises(ReviewError) as caught:
            operation()
        self.assertEqual(caught.exception.status, status, str(caught.exception))

    def test_missing_reads_and_failed_cas_do_not_create_storage(self):
        for kind in ("filesystem", "sqlite"):
            with self.subTest(kind=kind):
                store, _, path = self.stores(kind, kind)
                graphs = DocumentGraphRepository(store)
                reviews = DocumentHumanReviewRepository(store, graphs)
                self.assert_status(404, graphs.list_graphs)
                self.assert_status(404, lambda: reviews.get_review("uninitialized"))
                self.assert_status(409, lambda: store.compare_and_swap("review.json", {"changed": True}, "missing"))
                self.assertFalse(path.exists())
                if kind == "sqlite":
                    self.assertFalse(path.parent.exists())

    def test_create_and_stale_cas_are_atomic_across_independent_instances(self):
        for kind in ("filesystem", "sqlite"):
            with self.subTest(kind=kind):
                first, second, _ = self.stores(kind, kind)
                first.create("records/example.json", {"state": {"version": 1}, "audit": ["created"]})
                old, first_token = first.read("records/example.json")
                _, second_token = second.read("records/example.json")
                new = {"state": {"version": 2}, "audit": ["created", "updated"]}
                first.compare_and_swap("records/example.json", new, first_token)
                self.assert_status(409, lambda: second.compare_and_swap("records/example.json", old, second_token))
                self.assert_status(409, lambda: second.create("records/example.json", old))
                self.assertEqual(second.read("records/example.json")[0], new)
                self.assertNotEqual(second.read("records/example.json")[1], first_token)

    def test_competing_writers_cannot_both_commit_the_same_revision(self):
        for kind in ("filesystem", "sqlite"):
            with self.subTest(kind=kind):
                first, second, _ = self.stores(kind, kind)
                first.create("shared.json", {"winner": None})
                barrier = threading.Barrier(2)
                results = []

                def write(store, name):
                    _, token = store.read("shared.json")
                    barrier.wait(timeout=3)
                    try:
                        store.compare_and_swap("shared.json", {"winner": name}, token)
                    except ReviewError as error:
                        results.append((name, error.status))
                    else:
                        results.append((name, 200))

                threads = [threading.Thread(target=write, args=(first, "one")),
                           threading.Thread(target=write, args=(second, "two"))]
                for thread in threads:
                    thread.start()
                for thread in threads:
                    thread.join(timeout=5)
                    self.assertFalse(thread.is_alive())
                self.assertEqual(sorted(status for _, status in results), [200, 409])
                winner = next(name for name, status in results if status == 200)
                self.assertEqual(first.read("shared.json")[0], {"winner": winner})

    def test_filesystem_failed_replace_keeps_document_and_releases_lock(self):
        store, second, path = self.stores("filesystem", "json")
        store.create("review.json", {"version": 1})
        before, token = store.read("review.json")
        with patch("filesystem_repository.os.replace", side_effect=OSError("interrupted write")):
            self.assert_status(500, lambda: store.compare_and_swap("review.json", {"version": 2}, token))
        self.assertEqual(store.read("review.json"), (before, token))
        self.assertEqual(list(path.glob(".document-*")), [])
        second.compare_and_swap("review.json", {"version": 2}, token)
        self.assertEqual(store.read("review.json")[0], {"version": 2})

    def test_filesystem_rejects_traversal_and_symlink_document_paths(self):
        store, _, path = self.stores("filesystem", "json")
        for key in ("../outside.json", "/absolute.json", "a//b.json", "a/./b.json", "a\\b.json"):
            with self.subTest(key=key):
                self.assert_status(400, lambda: store.create(key, {"bad": True}))
        self.assertFalse(path.exists())
        path.mkdir()
        outside = self.root / "outside"
        outside.mkdir()
        (path / "linked").symlink_to(outside, target_is_directory=True)
        self.assert_status(409, lambda: store.create("linked/review.json", {"bad": True}))
        self.assertEqual(list(outside.iterdir()), [])

    def test_document_size_guard_and_invalid_json_do_not_replace_current_state(self):
        for kind in ("filesystem", "sqlite"):
            with self.subTest(kind=kind):
                first, _, _ = self.stores(kind, kind)
                first.max_bytes = 50
                first.create("small.json", {"ok": True})
                original, token = first.read("small.json")
                self.assert_status(413, lambda: first.compare_and_swap("small.json", {"huge": "x" * 60}, token))
                self.assert_status(400, lambda: first.compare_and_swap("small.json", {"number": float("nan")}, token))
                self.assertEqual(first.read("small.json"), (original, token))


class MixedRepositoryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.graph = json.loads((SKILL / "assets" / "example-graph.json").read_text())
        self.graph_id = "project:billing/checkout α"
        self.revision = "revision:feature/a+1"
        self.graph_store = FilesystemDocumentStore(self.root / "graphs")
        self.review_store = SQLiteDocumentStore(self.root / "reviews.sqlite3")
        self.graphs = DocumentGraphRepository(self.graph_store)
        self.reviews = DocumentHumanReviewRepository(self.review_store, self.graphs)
        self.graphs.publish_graph(self.graph_id, self.revision, self.graph)
        self.reviews.initialize_review(self.graph_id, self.revision)

    def assert_status(self, status, operation):
        with self.assertRaises(ReviewError) as caught:
            operation()
        self.assertEqual(caught.exception.status, status, str(caught.exception))

    def annotate(self, text="Review this edge case", target_type="graph", target_id=None):
        state = self.reviews.get_review(self.graph_id)
        return self.reviews.create_annotation(self.graph_id,
                                             {"target_type": target_type, "target_id": target_id,
                                              "kind": "question", "text": text}, state["state_version"])

    def test_mixed_backends_and_opaque_identifiers_survive_reopen(self):
        state = self.annotate()
        reopened = DocumentHumanReviewRepository(SQLiteDocumentStore(self.review_store.path),
                                                DocumentGraphRepository(FilesystemDocumentStore(self.graph_store.path)))
        self.assertEqual(reopened.get_review(self.graph_id), state)
        self.assertEqual(state["graph_id"], self.graph_id)
        self.assertEqual(state["graph_revision"], self.revision)
        self.assertEqual(state["rounds"][0]["graph_revision"], self.revision)
        self.assertEqual(self.graphs.list_graphs()[0]["id"], self.graph_id)
        self.assertNotIn("graph", state)
        document, _ = self.review_store.read(review_key(self.graph_id))
        self.assertNotIn("graph", document["state"])
        self.assertEqual(document["audit"][-1]["action"], "annotated")
        self.assert_status(404, lambda: self.graph_store.read(review_key(self.graph_id)))
        self.assert_status(404, lambda: self.review_store.read(CATALOG_KEY))

    def test_reverse_mixed_binding_with_sqlite_graphs_and_filesystem_reviews(self):
        graphs = DocumentGraphRepository(SQLiteDocumentStore(self.root / "other-graphs.sqlite3"))
        reviews = DocumentHumanReviewRepository(FilesystemDocumentStore(self.root / "other-reviews"), graphs)
        graphs.publish_graph(self.graph_id, self.revision, self.graph)
        initial = reviews.initialize_review(self.graph_id, self.revision)
        saved = reviews.create_annotation(self.graph_id, {"target_type": "graph", "target_id": None,
                                                        "kind": "note", "text": "Saved on disk"}, initial["state_version"])
        self.assertEqual(reviews.get_review(self.graph_id), saved)
        self.assertEqual(reviews.list_pending_feedback(self.graph_id)["graph_revision"], self.revision)

    def test_feedback_ack_is_exact_and_cannot_resolve_or_approve_a_note(self):
        state = self.annotate()
        note = state["annotations"][0]
        inbox = self.reviews.list_pending_feedback(self.graph_id)
        self.assertEqual(inbox["annotations"], [note])
        self.assertEqual(inbox["graph_revision"], self.revision)
        processed = self.reviews.mark_feedback_processed(self.graph_id, note["id"], 1, "needs_clarification",
                                                       "Asked a follow-up question", "agent:test", state["state_version"])
        self.assertEqual(processed["annotations"][0]["status"], "open")
        self.assertEqual(processed["annotations"][0]["processing_receipt"]["graph_revision"], self.revision)
        self.assertEqual(self.reviews.list_pending_feedback(self.graph_id)["annotations"], [])
        self.assert_status(409, lambda: self.reviews.approve_review(self.graph_id, "Approved", True, processed["state_version"]))
        edited = self.reviews.update_annotation(self.graph_id, note["id"], {"text": "A newer human clarification"}, processed["state_version"])
        before = self.review_store.read(review_key(self.graph_id))
        self.assert_status(409, lambda: self.reviews.mark_feedback_processed(self.graph_id, note["id"], 1,
                           "addressed", "Stale work", "agent:test", edited["state_version"]))
        self.assert_status(409, lambda: self.reviews.mark_feedback_processed(self.graph_id, note["id"], 2,
                           "addressed", "Stale state", "agent:test", processed["state_version"]))
        self.assertEqual(self.review_store.read(review_key(self.graph_id)), before)
        self.assertEqual(edited["annotations"][0]["feedback_revision"], 2)

    def test_processing_receipt_preserves_approval_but_edit_and_delete_invalidate_it(self):
        state = self.annotate()
        note = state["annotations"][0]
        resolved = self.reviews.resolve_annotation(self.graph_id, note["id"], "addressed", "Settled", state["state_version"])
        approved = self.reviews.approve_review(self.graph_id, "I accept this scope", True, resolved["state_version"])
        processed = self.reviews.mark_feedback_processed(self.graph_id, note["id"], 2, "addressed",
                                                       "Stored the decision", "agent:test", approved["state_version"])
        self.assertEqual(processed["approval"], approved["approval"])
        self.assertEqual(review_digest(processed), review_digest(approved))
        edited = self.reviews.update_annotation(self.graph_id, note["id"], {"kind": "correction"}, processed["state_version"])
        self.assertIsNone(edited["approval"])
        self.assertEqual(edited["annotations"][0]["status"], "open")
        self.assertEqual(edited["annotations"][0]["feedback_revision"], 3)
        for field in ("id", "target_type", "target_id", "created_at", "round", "source"):
            self.assertEqual(edited["annotations"][0][field], note[field])
        deleted = self.reviews.delete_annotation(self.graph_id, note["id"], edited["state_version"])
        self.assertEqual(self.reviews.list_annotations(self.graph_id), [])
        tombstone = self.reviews.get_annotation(self.graph_id, note["id"], include_deleted=True)
        self.assertEqual(tombstone["feedback_revision"], 4)
        self.assertEqual(tombstone["text"], note["text"])
        self.assertEqual(self.reviews.list_pending_feedback(self.graph_id)["annotations"], [tombstone])
        self.assert_status(409, lambda: self.reviews.reopen_annotation(self.graph_id, note["id"], deleted["state_version"]))
        self.assert_status(404, lambda: self.reviews.get_annotation(self.graph_id, note["id"]))

    def test_review_request_remains_pending_until_completed_by_shared_actions(self):
        state = self.annotate()
        note = state["annotations"][0]
        requested = self.reviews.request_review(self.graph_id, state["state_version"])
        self.assert_status(409, lambda: self.reviews.complete_review(self.graph_id, "Not settled", requested["state_version"]))
        resolved_state = self.reviews.session(self.graph_id).resolve(note["id"], "addressed", "Recorded decision", "agent", requested["state_version"])
        self.assertEqual(resolved_state["annotations"][0]["feedback_revision"], 1)
        self.assert_status(409, lambda: self.reviews.approve_review(self.graph_id, "Approved", True, resolved_state["state_version"]))
        completed = self.reviews.complete_review(self.graph_id, "All feedback settled", resolved_state["state_version"])
        self.assertFalse(completed["review_requested"])
        approved = self.reviews.approve_review(self.graph_id, "Approve this scope", True, completed["state_version"])
        self.assertIsNotNone(approved["approval"])
        # Operational receipt remains a separate lifecycle from disposition.
        self.assertEqual(len(self.reviews.list_pending_feedback(self.graph_id)["annotations"]), 1)

    def test_review_uses_pinned_revision_even_after_catalog_advances(self):
        changed = deepcopy(self.graph)
        old_id = changed["nodes"][0]["id"]
        new_id = "new-node-for-next-revision"
        changed["nodes"][0]["id"] = new_id
        for edge in changed["dependencies"]:
            for field in ("from", "to"):
                if edge[field] == old_id:
                    edge[field] = new_id
        self.graphs.publish_graph(self.graph_id, "revision:next/2", changed)
        self.assertEqual(self.graphs.get_graph(self.graph_id)["revision"], "revision:next/2")
        self.assertEqual(self.graphs.get_graph(self.graph_id, self.revision)["graph"], self.graph)
        state = self.annotate(target_type="node", target_id=old_id)
        self.assertEqual(state["graph_revision"], self.revision)
        self.assert_status(400, lambda: self.reviews.create_annotation(self.graph_id,
                           {"target_type": "node", "target_id": new_id, "kind": "note", "text": "Wrong revision"}, state["state_version"]))
        note = state["annotations"][0]
        processed = self.reviews.mark_feedback_processed(self.graph_id, note["id"], 1, "addressed",
                                                       "Reviewed the pinned snapshot", "agent:test", state["state_version"])
        self.assertEqual(processed["annotations"][0]["processing_receipt"]["graph_revision"], self.revision)
        self.assertEqual(processed["annotations"][0]["processing_receipt"]["graph_digest"], digest(self.graph))

    def test_same_revision_is_immutable_and_mismatched_graph_digest_blocks_review(self):
        changed = deepcopy(self.graph)
        changed["nodes"][0]["summary"] += " Changed."
        self.assert_status(409, lambda: self.graphs.publish_graph(self.graph_id, self.revision, changed))
        key = graph_key(self.graph_id, self.revision)
        snapshot, token = self.graph_store.read(key)
        snapshot.update(graph=changed, digest=digest(changed))
        # Deliberate storage tampering: even a self-consistent replacement must
        # fail the review's independently pinned digest.
        self.graph_store.compare_and_swap(key, snapshot, token)
        self.assert_status(409, lambda: self.reviews.get_review(self.graph_id))

    def test_new_proposal_reopens_changed_targets_and_preserves_feedback_receipts(self):
        state = self.annotate(target_type="node", target_id=self.graph["nodes"][0]["id"])
        note = state["annotations"][0]
        resolved = self.reviews.resolve_annotation(self.graph_id, note["id"], "addressed", "Settled", state["state_version"])
        processed = self.reviews.mark_feedback_processed(self.graph_id, note["id"], 2, "addressed", "Stored decision",
                                                        "agent:test", resolved["state_version"])
        approved = self.reviews.approve_review(self.graph_id, "Approve old proposal", True, processed["state_version"])
        changed = deepcopy(self.graph)
        changed["nodes"][0]["summary"] += " Relevant changed behavior."
        proposed = self.reviews.session(self.graph_id).propose(changed, "Recheck changed behavior", approved["state_version"])
        self.assertEqual(proposed["round"], 2)
        self.assertNotEqual(proposed["graph_revision"], self.revision)
        self.assertNotEqual(proposed["graph_revision"], "2")
        reopened = proposed["annotations"][0]
        self.assertEqual(reopened["status"], "open")
        self.assertEqual(reopened["feedback_revision"], 3)
        self.assertEqual(reopened["processed_revision"], 2)
        self.assertEqual(reopened["processing_receipt"], processed["annotations"][0]["processing_receipt"])
        self.assertEqual(reopened["processing_status"], "unprocessed")
        self.assertIsNone(proposed["approval"])
        self.assertEqual(self.graphs.get_graph(self.graph_id, proposed["graph_revision"])["graph"], changed)
        self.assertEqual(self.graphs.get_graph(self.graph_id, self.revision)["graph"], self.graph)
        raw, _ = self.review_store.read(review_key(self.graph_id))
        self.assertNotIn("graph", raw["state"])
        self.assertEqual(raw["audit"][-1]["payload"]["reopened_annotations"][0]["annotation_id"], note["id"])

    def test_stale_proposal_fails_before_publication_and_intervening_edit_survives(self):
        state = self.annotate()
        note_id = state["annotations"][0]["id"]
        changed = deepcopy(self.graph)
        changed["nodes"][0]["summary"] += " A proposed change."
        self.assert_status(409, lambda: self.reviews.session(self.graph_id).propose(changed, "Stale proposal", 1,
                           revision="should-not-exist"))
        self.assert_status(404, lambda: self.graphs.get_graph(self.graph_id, "should-not-exist"))
        self.assertEqual(self.graphs.get_graph(self.graph_id)["revision"], self.revision)

        publish = self.graphs.publish_graph
        def interleaved_publish(*args, **kwargs):
            snapshot = publish(*args, **kwargs)
            self.reviews.update_annotation(self.graph_id, note_id, {"text": "Human edit during publication"}, state["state_version"])
            return snapshot

        with patch.object(self.graphs, "publish_graph", side_effect=interleaved_publish):
            self.assert_status(409, lambda: self.reviews.session(self.graph_id).propose(changed, "Racing proposal",
                               state["state_version"], revision="published-during-race"))
        current = self.reviews.get_review(self.graph_id)
        self.assertEqual(current["graph_revision"], self.revision)
        self.assertEqual(current["round"], 1)
        self.assertEqual(current["annotations"][0]["text"], "Human edit during publication")
        self.assertEqual(current["state_version"], state["state_version"] + 1)
        self.assertEqual(self.graphs.get_graph(self.graph_id)["revision"], "published-during-race")
        raw, _ = self.review_store.read(review_key(self.graph_id))
        self.assertEqual(raw["audit"][-1]["action"], "annotation_edited")
        self.assertFalse(any(event["action"] == "proposed" for event in raw["audit"]))

    def test_stale_review_mutation_and_other_graph_ids_do_not_change_audit(self):
        state = self.annotate()
        note = state["annotations"][0]
        updated = self.reviews.update_annotation(self.graph_id, note["id"], {"text": "Current"}, state["state_version"])
        before = self.review_store.read(review_key(self.graph_id))
        self.assert_status(409, lambda: self.reviews.delete_annotation(self.graph_id, note["id"], state["state_version"]))
        self.assert_status(404, lambda: self.reviews.get_review("other-graph"))
        self.assert_status(400, lambda: self.reviews.update_annotation(self.graph_id, note["id"], {"source": "agent"}, updated["state_version"]))
        self.assertEqual(self.review_store.read(review_key(self.graph_id)), before)

    def test_import_session_preserves_approval_versions_audit_and_history(self):
        source = Store(self.root / "legacy")
        source.initialize("Existing review")
        first = source.propose(self.graph, "Original", 1)
        state = source.annotate("graph", None, "correction", "Human feedback", "chat", "message:one", first["state_version"])
        note_id = state["annotations"][0]["id"]
        resolved = source.resolve(note_id, "addressed", "Settled", "agent", state["state_version"])
        changed = deepcopy(self.graph)
        changed["nodes"][0]["summary"] += " An updated proposal."
        proposed = source.propose(changed, "Revised", resolved["state_version"])
        resolved = source.resolve(note_id, "addressed", "Rechecked revised proposal", "agent", proposed["state_version"])
        approved = source.approve("Approve this revision", "chat", "message:approval", True, resolved["state_version"])
        with source.connection() as connection:
            source_audit = [json.loads(row[0]) for row in connection.execute("SELECT content FROM audit ORDER BY id")]
        imported = self.reviews.import_session(source)
        identifier = approved["session_id"]
        self.assertEqual(imported["graph_id"], identifier)
        self.assertEqual(imported["session_id"], identifier)
        self.assertEqual(imported["state_version"], approved["state_version"])
        self.assertEqual(imported["annotations"], approved["annotations"])
        self.assertEqual(imported["approval"], approved["approval"])
        self.assertEqual(review_digest(imported), approved["approval"]["review_digest"])
        self.assertEqual([item["graph_revision"] for item in imported["rounds"]], ["1", "2"])
        self.assertEqual(self.graphs.get_graph(identifier, "1")["graph"], self.graph)
        self.assertEqual(self.graphs.get_graph(identifier, "2")["graph"], changed)
        self.assertEqual(self.reviews.get_review(identifier), imported)
        raw, _ = self.review_store.read(review_key(identifier))
        self.assertEqual(raw["audit"], source_audit)
        self.assert_status(409, lambda: self.reviews.import_session(source))


if __name__ == "__main__":
    unittest.main()
