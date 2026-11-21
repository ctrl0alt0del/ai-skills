"""Persistence-independent annotation, feedback, and human approval actions.

A host implements state() and mutate(expected_version, action, actor,
operation, invalidate=True). mutate must check the version and persist the
changed state plus its audit event atomically. The operation's second argument
is deliberately unused by these shared actions (legacy SQLite passes a
connection; document repositories pass None).
"""

import json
import uuid

from graph_contract import (DISPOSITIONS, KINDS, PROCESSING_OUTCOMES, TARGET_TYPES,
                            ReviewError, advance_feedback, canonical, digest,
                            normalize_feedback, review_digest, selected, text_value,
                            timestamp, validate_graph)


class ReviewActions:
    def apply_proposal(self, state, graph, summary, graph_revision=None):
        """Apply one proposal transition inside the host's atomic mutation.

        Persistence owns immutable snapshot publication. This shared transition
        owns round numbering, direct-target changes, reopening, and pending
        feedback revisions for both legacy and document-backed sessions.
        """
        number = state["round"] + 1
        graph_hash = digest(graph)
        reopened = []
        if state["graph_digest"] != graph_hash:
            for note in state["annotations"]:
                if note["status"] == "resolved" and self.target_snapshot(state["graph"], note) != self.target_snapshot(graph, note):
                    reopened.append({"annotation_id": note["id"], "previous_resolution": note["resolution"]})
                    note.update(status="open", resolution=None)
                    advance_feedback(note)
        metadata = {"round": number, "digest": graph_hash, "summary": summary,
                    "created_at": timestamp(), "node_count": len(graph["nodes"]),
                    "dependency_count": len(graph["dependencies"])}
        if graph_revision is not None:
            metadata["graph_revision"] = graph_revision
            state["graph_revision"] = graph_revision
        state.update(round=number, graph=graph, graph_digest=graph_hash, review_requested=False)
        state["rounds"].append(metadata)
        return metadata, reopened

    @staticmethod
    def target_snapshot(graph, note):
        if graph is None or note["target_type"] == "graph":
            return graph
        if note["target_type"] == "claim":
            records = [claim for node in graph["nodes"] for claim in node["claims"]]
        else:
            records = graph["nodes"] if note["target_type"] == "node" else graph["dependencies"]
        return next((item for item in records if item["id"] == note["target_id"]), None)

    @staticmethod
    def target_exists(state, target_type, target_id):
        selected(target_type, TARGET_TYPES, "target_type")
        if target_type == "graph":
            if target_id is not None:
                raise ReviewError("Graph annotations must use target_id=null.")
            return
        text_value(target_id, "target_id", 120)
        graph = state["graph"]
        if graph is None:
            raise ReviewError("No proposal exists for this target.")
        records = graph["nodes"] if target_type == "node" else graph["dependencies"]
        if target_type == "claim":
            records = [claim for node in graph["nodes"] for claim in node["claims"]]
        if not any(record["id"] == target_id for record in records):
            raise ReviewError("Annotation target does not exist in the current proposal.")

    def annotate(self, target_type, target_id, kind, text, source, source_ref, expected_version):
        selected(kind, KINDS, "kind")
        selected(source, {"ui", "chat", "agent"}, "source")
        text = text_value(text, "text")
        if source_ref is not None:
            source_ref = text_value(source_ref, "source_ref", 2000)

        def operation(state, connection):
            self.target_exists(state, target_type, target_id)
            annotation = {
                "id": "note-" + uuid.uuid4().hex[:16], "target_type": target_type,
                "target_id": target_id, "kind": kind, "text": text, "source": source,
                "source_ref": source_ref, "round": state["round"], "created_at": timestamp(),
                "status": "open", "resolution": None,
                "feedback_revision": 0 if source == "agent" else 1,
                "processed_revision": 0, "processing_receipt": None,
            }
            normalize_feedback(annotation)
            state["annotations"].append(annotation)
            return annotation

        return self.mutate(expected_version, "annotated", source, operation)

    def resolve(self, annotation_id, disposition, text, source, expected_version):
        selected(disposition, DISPOSITIONS, "disposition")
        selected(source, {"ui", "chat", "agent"}, "source")
        text = text_value(text, "resolution text")

        def operation(state, connection):
            note = self._note(state, annotation_id)
            if note["status"] != "open":
                raise ReviewError("Annotation is already resolved. Reopen it before changing its resolution.", 409)
            resolution = {"disposition": disposition, "text": text, "source": source,
                          "round": state["round"], "at": timestamp()}
            note.update(status="resolved", resolution=resolution)
            if source != "agent":
                advance_feedback(note)
            return {"annotation_id": annotation_id, "resolution": resolution}

        return self.mutate(expected_version, "resolved", source, operation)

    def edit_annotation(self, annotation_id, changes, source, expected_version):
        """Edit human-authored content without replacing identity, target, or history."""
        selected(source, {"ui", "chat", "agent"}, "source")
        if not isinstance(changes, dict) or not changes or changes.keys() - {"text", "kind"}:
            raise ReviewError("Annotation edits require text and/or kind; other fields are immutable.")
        changes = dict(changes)
        if "text" in changes:
            changes["text"] = text_value(changes["text"], "text")
        if "kind" in changes:
            selected(changes["kind"], KINDS, "kind")

        def operation(state, connection):
            note = self._note(state, annotation_id)
            if note["status"] == "deleted":
                raise ReviewError("Deleted annotations cannot be edited.", 409)
            previous = json.loads(canonical(note))
            note.update(changes)
            note.update(status="open", resolution=None, updated_at=timestamp(), updated_by=source)
            advance_feedback(note)
            return {"annotation_id": annotation_id, "previous": previous,
                    "annotation": json.loads(canonical(note))}

        return self.mutate(expected_version, "annotation_edited", source, operation)

    def delete_annotation(self, annotation_id, source, expected_version):
        """Tombstone a note; preserve its original content and audit for past reviews."""
        selected(source, {"ui", "chat", "agent"}, "source")

        def operation(state, connection):
            note = self._note(state, annotation_id)
            if note["status"] == "deleted":
                raise ReviewError("Annotation is already deleted.", 409)
            previous = json.loads(canonical(note))
            note.update(status="deleted", deleted_at=timestamp(), deleted_by=source)
            advance_feedback(note)
            return {"annotation_id": annotation_id, "previous": previous,
                    "annotation": json.loads(canonical(note))}

        return self.mutate(expected_version, "annotation_deleted", source, operation)

    @staticmethod
    def _note(state, identifier):
        for note in state["annotations"]:
            if note["id"] == identifier:
                return note
        raise ReviewError("Annotation not found.", 404)

    def reopen(self, annotation_id, expected_version, source="agent"):
        selected(source, {"ui", "chat", "agent"}, "source")

        def operation(state, connection):
            note = self._note(state, annotation_id)
            if note["status"] == "deleted":
                raise ReviewError("Deleted annotations cannot be reopened. Create a new note.", 409)
            if note["status"] == "open":
                raise ReviewError("Annotation is already open.", 409)
            previous = note["resolution"]
            note.update(status="open", resolution=None)
            advance_feedback(note)
            return {"annotation_id": annotation_id, "previous_resolution": previous}

        return self.mutate(expected_version, "reopened", source, operation)

    def list_pending_feedback(self):
        """Return one consistent inbox snapshot, including withdrawn notes."""
        state = self.state()
        return {
            "schema_version": "behavior-review-feedback/1",
            "graph_id": state.get("graph_id", state["session_id"]),
            "graph_revision": state.get("graph_revision", str(state["round"])),
            "graph_digest": state["graph_digest"], "state_version": state["state_version"],
            "review_requested": state["review_requested"],
            "annotations": [note for note in state["annotations"]
                            if note["processing_status"] == "unprocessed"],
        }

    def mark_feedback_processed(self, annotation_id, feedback_revision, outcome,
                                summary, processor, expected_version):
        """Acknowledge only the inspected revision, after its work is durable.

        The caller owns evidence investigation and saving the resulting work.
        This records that claim; it cannot prove work happened or approve policy.
        A stale note or state fails atomically and remains pending for rereading.
        """
        if type(feedback_revision) is not int or feedback_revision < 1:
            raise ReviewError("feedback_revision must be a positive integer.")
        selected(outcome, PROCESSING_OUTCOMES, "processing outcome")
        summary = text_value(summary, "processing summary")
        processor = text_value(processor, "processor", 200)

        def operation(state, connection):
            note = self._note(state, annotation_id)
            if note["feedback_revision"] != feedback_revision:
                raise ReviewError("Feedback changed. Inspect the current annotation revision before marking it processed.", 409)
            if note["processed_revision"] >= feedback_revision:
                raise ReviewError("This feedback revision is already processed. Read its receipt before retrying.", 409)
            receipt = {
                "feedback_revision": feedback_revision, "outcome": outcome,
                "summary": summary, "processor": processor, "processed_at": timestamp(),
                "graph_revision": state.get("graph_revision", str(state["round"])),
                "graph_digest": state["graph_digest"],
            }
            note.update(processed_revision=feedback_revision, processing_status="processed",
                        processing_receipt=receipt)
            return {"annotation_id": annotation_id, "receipt": receipt}

        return self.mutate(expected_version, "feedback_processed", "agent", operation, invalidate=False)

    def request_review(self, expected_version):
        def operation(state, connection):
            state["review_requested"] = True
            return {"message": "Feedback saved. Return to chat and ask the agent to review it."}

        return self.mutate(expected_version, "review_requested", "ui", operation)

    def complete_review(self, summary, expected_version):
        summary = text_value(summary, "summary")

        def operation(state, connection):
            if not state["graph"] or not state["round"]:
                raise ReviewError("Present a proposal before completing review.", 409)
            if any(note["status"] == "open" for note in state["annotations"]):
                raise ReviewError("Disposition all open annotations before completing review.", 409)
            state["review_requested"] = False
            return {"summary": summary}

        return self.mutate(expected_version, "review_completed", "agent", operation)

    @staticmethod
    def settled(state):
        if state["graph"] is None or state["round"] < 1:
            raise ReviewError("There is no reviewable proposal to approve.", 409)
        validate_graph(state["graph"])
        if digest(state["graph"]) != state["graph_digest"]:
            raise ReviewError("The graph snapshot does not match its recorded digest.", 409)
        count = sum(note["status"] == "open" for note in state["annotations"])
        if count:
            raise ReviewError(f"Resolve or explicitly disposition all {count} open annotations first.", 409)
        if state["review_requested"]:
            raise ReviewError("A requested review is still pending. The agent must present the reviewed round first.", 409)

    def approve(self, text, source, source_ref, acknowledge_limits, expected_version):
        selected(source, {"ui", "chat"}, "approval source")
        text = text_value(text, "approval text")
        if acknowledge_limits is not True:
            raise ReviewError("Explicit acknowledgment of scope, uncertainties, and coverage limits is required.")
        if source == "chat":
            source_ref = text_value(source_ref, "source_ref", 2000)

        def operation(state, connection):
            self.settled(state)
            approval = {
                "round": state["round"], "digest": state["graph_digest"], "source": source,
                "source_ref": source_ref, "text": text, "created_at": timestamp(),
                "acknowledge_limits": True, "review_digest": review_digest(state),
                "reviewed_state_version": state["state_version"],
            }
            state["approval"] = approval
            state["finalized"] = None
            return approval

        return self.mutate(expected_version, "approved", source, operation, invalidate=False)

