"""Regression checks for two-investigator/judge artifact gates.

Run from the repository root with:
    python3 -m unittest discover -s tests -p 'test_refinement_review.py'

REFINEMENT_SKILL_DIR can point to an installed skill while developing it.
"""

import copy
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

sys.dont_write_bytecode = True
SKILL = Path(os.environ.get(
    "REFINEMENT_SKILL_DIR",
    str(Path(__file__).resolve().parents[1] / "skills" / "requirement-refinement"),
))
SCRIPT = SKILL / "scripts" / "review_workflow.py"
SPEC = importlib.util.spec_from_file_location("review_workflow", SCRIPT)
workflow = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(workflow)


def seed():
    return {
        "schema_version": "1.0",
        "requirement": {
            "original": "Allow customers to cancel selected seats in a booking.",
            "interpretation": "Refine partial cancellation of a booking.",
            "assumptions": [],
        },
        "analysis": {
            "mode": "requirement-only", "revision": None, "scope": ["Booking cancellation"],
        },
        "evidence": [{
            "id": "E-001", "kind": "requirement", "location": "input:requirement",
            "summary": "The requested flow cancels a subset of seats.",
        }],
        "dependencies": [], "findings": [], "settled": [],
        "coverage": {
            "checked": ["Supplied requirement"], "blind_spots": ["No implementation supplied"],
            "unexpanded": [], "stop_reason": "Frozen input for independent investigation.",
        },
    }


def finding(identifier="Q-001"):
    return {
        "id": identifier, "category": "decision", "capability": "Cancellation",
        "priority": "blocking", "priority_reason": "Defines which seats remain usable.",
        "question": "What should happen to the uncancelled seats?",
        "scenario": "A customer cancels one seat of a two-seat booking.",
        "affected_behaviors": ["Remaining seat eligibility"],
        "impact": "The remaining seat needs a defined usable state.",
        "evidence_ids": ["E-001"], "dependency_ids": [], "basis": "inferred",
        "reasoning": "Partial cancellation introduces mixed states within one booking.",
        "options": [], "suggested_owner": "Product",
    }


def investigation(packet, role):
    report = copy.deepcopy(packet["context"])
    report["findings"] = [finding()]
    return {
        "schema_version": "refinement-investigation/1",
        "run_id": packet["run_id"], "packet_digest": packet["packet_digest"],
        "role": role, "agent_id": role + "-agent", "report": report,
    }


def case(mode="isolated-agents"):
    packet = workflow.prepare_packet(
        seed(), mode, "coordinator",
        "Host does not expose isolated agents." if mode == "sequential-fallback" else None,
    )
    business, failure = (investigation(packet, role) for role in ("business", "failure"))
    final_report = copy.deepcopy(packet["context"])
    final_report["findings"] = [finding("Q-001"), finding("Q-002")]
    judgment = {
        "schema_version": "refinement-judgment/1",
        "run_id": packet["run_id"],
        "judge_input_digest": workflow.assemble(packet, business, failure)["judge_input_digest"],
        "agent_id": "judge-agent", "report": final_report,
        "verdicts": [
            {"candidate_id": role + ":Q-001", "action": "accept",
             "finding_ids": ["Q-00" + str(index)], "rationale": "Preserve the concrete decision."}
            for index, role in enumerate(("business", "failure"), 1)
        ],
        "added_findings": [],
        "source_checks": [{
            "location": "input:requirement", "result": "packet-only",
            "note": "The user's requirement is present verbatim in the shared packet.",
        }],
    }
    return packet, business, failure, judgment


class ReviewWorkflowTests(unittest.TestCase):
    def assert_invalid(self, values, pattern):
        with self.assertRaisesRegex(workflow.WorkflowError, pattern):
            workflow.validate_judgment(*values)

    def test_valid_review_preserves_report_schema_and_local_ids(self):
        values = case()
        assembled = workflow.validate_judgment(*values)
        self.assertEqual(assembled["investigations"]["business"]["report"]["findings"][0]["id"],
                         assembled["investigations"]["failure"]["report"]["findings"][0]["id"])
        self.assertEqual(workflow._report.validate_report(values[3]["report"]), [])

    def test_packet_tampering_and_stale_investigation(self):
        values = case()
        changed = copy.deepcopy(values[0])
        changed["context"]["analysis"]["revision"] = "different"
        with self.assertRaisesRegex(workflow.WorkflowError, "packet_digest"):
            workflow.validate_packet(changed)
        values[1]["packet_digest"] = "0" * 64
        self.assert_invalid(values, "stale or different packet")

    def test_changed_investigation_requires_fresh_judge(self):
        values = case()
        values[1]["report"]["findings"][0]["impact"] = "A materially revised consequence."
        self.assert_invalid(values, "stale or different inputs")

    def test_agent_identity_gates(self):
        for target, replacement in ((1, "coordinator"), (2, "business-agent"),
                                    (3, "business-agent"), (3, "coordinator")):
            with self.subTest(target=target, replacement=replacement):
                values = case()
                values[target]["agent_id"] = replacement
                if target == 3:
                    self.assert_invalid(values, "isolated judge")
                else:
                    with self.assertRaises(workflow.WorkflowError):
                        workflow.assemble(*values[:3])

    def test_wrong_role_is_not_accepted_by_assemble(self):
        values = case()
        values[1]["role"] = "failure"
        with self.assertRaisesRegex(workflow.WorkflowError, "expected business"):
            workflow.assemble(*values[:3])

    def test_missing_and_duplicate_candidate_verdicts(self):
        values = case()
        values[3]["verdicts"].pop()
        self.assert_invalid(values, "missing candidates")
        values = case()
        values[3]["verdicts"].append(copy.deepcopy(values[3]["verdicts"][0]))
        self.assert_invalid(values, "duplicate verdict")

    def test_unknown_and_duplicate_final_references(self):
        for refs, expected in ((["Q-999"], "unknown final finding"),
                               (["Q-001", "Q-001"], "duplicate final finding")):
            with self.subTest(refs=refs):
                values = case()
                values[3]["verdicts"][0]["finding_ids"] = refs
                self.assert_invalid(values, expected)

    def test_reject_cannot_keep_a_final_mapping(self):
        values = case()
        values[3]["verdicts"][0]["action"] = "reject"
        self.assert_invalid(values, "rejected candidate must map to no")
        values[3]["verdicts"][0]["finding_ids"] = []
        values[3]["report"]["findings"].pop(0)
        workflow.validate_judgment(*values)

    def test_merge_requires_multiple_source_candidates(self):
        values = case()
        values[3]["verdicts"][0]["action"] = "merge"
        self.assert_invalid(values, "at least two source candidates")
        values[3]["verdicts"][1].update(action="merge", finding_ids=["Q-001"])
        values[3]["report"]["findings"].pop()
        workflow.validate_judgment(*values)

    def test_shared_final_targets_require_explicit_consolidation(self):
        for actions in (("accept", "accept"), ("merge", "accept"), ("revise", "merge")):
            with self.subTest(actions=actions):
                values = case()
                values[3]["report"]["findings"].pop()
                for verdict, action in zip(values[3]["verdicts"], actions):
                    verdict.update(action=action, finding_ids=["Q-001"])
                self.assert_invalid(values, "shares final finding")

    def test_split_and_merge_can_be_combined(self):
        values = case()
        values[3]["verdicts"][0].update(action="revise", finding_ids=["Q-001", "Q-002"])
        values[3]["verdicts"][1].update(action="merge", finding_ids=["Q-001"])
        workflow.validate_judgment(*values)

    def test_accept_cannot_split(self):
        values = case()
        values[3]["verdicts"][0]["finding_ids"] = ["Q-001", "Q-002"]
        values[3]["verdicts"][1].update(action="reject", finding_ids=[])
        self.assert_invalid(values, "accept must map to exactly one")

    def test_orphan_findings_and_added_findings(self):
        values = case()
        values[3]["report"]["findings"].append(finding("Q-003"))
        self.assert_invalid(values, "no candidate mapping")
        values[3]["added_findings"] = [{"finding_id": "Q-003", "rationale": "New checked case."}]
        workflow.validate_judgment(*values)
        values[3]["added_findings"].append(copy.deepcopy(values[3]["added_findings"][0]))
        self.assert_invalid(values, "listed once")
        values = case()
        values[3]["added_findings"] = [{"finding_id": "Q-001", "rationale": "Already mapped."}]
        self.assert_invalid(values, "not mapped")

    def test_source_checks_cover_all_final_evidence(self):
        values = case()
        values[3]["source_checks"] = []
        self.assert_invalid(values, "missing final evidence locations")
        values[3]["source_checks"] = [
            {"location": "input:requirement", "result": "unavailable",
             "note": "Do not claim the source was reopened."},
            {"location": "other-source", "result": "reopened",
             "note": "Checked for a candidate that was rejected."},
        ]
        workflow.validate_judgment(*values)
        values[3]["source_checks"].append(copy.deepcopy(values[3]["source_checks"][0]))
        self.assert_invalid(values, "duplicate location")

    def test_original_requirement_and_analysis_cannot_change(self):
        for key, field in (("requirement", "original"), ("requirement", "interpretation"),
                           ("analysis", "revision")):
            with self.subTest(key=key, field=field):
                values = case()
                values[1]["report"][key][field] = "different"
                with self.assertRaisesRegex(workflow.WorkflowError, "differs from the frozen"):
                    workflow.validate_investigation(values[0], values[1])

    def test_fallback_is_explicit_and_allows_same_actor(self):
        with self.assertRaisesRegex(workflow.WorkflowError, "fallback_reason"):
            workflow.prepare_packet(seed(), "sequential-fallback", "coordinator")
        with self.assertRaisesRegex(workflow.WorkflowError, "must be null"):
            workflow.prepare_packet(seed(), "isolated-agents", "coordinator", "Unneeded reason")
        values = case("sequential-fallback")
        for artifact in values[1:]:
            artifact["agent_id"] = "coordinator"
        values[3]["judge_input_digest"] = workflow.assemble(*values[:3])["judge_input_digest"]
        workflow.validate_judgment(*values)

    def test_seed_must_not_contain_generated_findings(self):
        source = seed()
        source["findings"] = [finding()]
        with self.assertRaisesRegex(workflow.WorkflowError, "must be empty"):
            workflow.prepare_packet(source, "isolated-agents", "coordinator")

    def test_strict_json_and_unknown_fields(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "bad.json"
            for raw in ('{"x":1,"x":2}', '{"x":NaN}', '{"x":Infinity}'):
                path.write_text(raw)
                with self.assertRaises(ValueError):
                    workflow.load_json(path)
        values = case()
        values[3]["approval"] = "Invented extra field"
        self.assert_invalid(values, "unexpected approval")

    def test_finalize_refuses_overwrite_and_preserves_audit(self):
        values = case()
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "final"
            receipt = workflow.finalize(*values, out)
            report = workflow.load_json(out / "refinement.json")
            audit = workflow.load_json(out / "review.json")
            self.assertEqual(report, values[3]["report"])
            self.assertEqual(audit["report_digest"], workflow.digest(report))
            self.assertEqual(audit["judgment_digest"], workflow.digest(values[3]))
            self.assertEqual(audit["investigations"]["business"], values[1])
            self.assertEqual(receipt["candidates"], 2)
            before = (out / "refinement.json").read_bytes()
            with self.assertRaises(FileExistsError):
                workflow.finalize(*values, out)
            self.assertEqual((out / "refinement.json").read_bytes(), before)

    def test_invalid_judge_writes_nothing(self):
        values = case()
        values[3]["verdicts"] = []
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "final"
            with self.assertRaises(workflow.WorkflowError):
                workflow.finalize(*values, out)
            self.assertFalse(out.exists())

    def test_failed_write_removes_its_partial_file_and_preserves_existing_file(self):
        real_open = Path.open
        def failing_open(path, *args, **kwargs):
            stream = real_open(path, *args, **kwargs)
            class FailingStream:
                def __enter__(self):
                    return self
                def __exit__(self, *unused):
                    stream.close()
                def fileno(self):
                    return stream.fileno()
                def write(self, payload):
                    stream.write(payload[:12])
                    stream.flush()
                    raise OSError("Simulated write failure")
            return FailingStream()
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "artifact.json"
            with mock.patch.object(Path, "open", side_effect=failing_open, autospec=True):
                with self.assertRaisesRegex(OSError, "Simulated write failure"):
                    workflow.write_new_json(path, {"payload": "partial"})
            self.assertFalse(path.exists())
            path.write_text("Existing user content")
            with mock.patch.object(Path, "open", side_effect=failing_open, autospec=True):
                with self.assertRaises(FileExistsError):
                    workflow.write_new_json(path, {"payload": "replacement"})
            self.assertEqual(path.read_text(), "Existing user content")

    def test_full_cli_workflow_and_legacy_report_validator(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "seed.json"
            source.write_text(json.dumps(seed()))
            def call(*args):
                result = subprocess.run([sys.executable, str(SCRIPT), *map(str, args)],
                                        capture_output=True, text=True)
                self.assertEqual(result.returncode, 0, result.stderr)
                return json.loads(result.stdout)
            packet_path = root / "packet.json"
            call("prepare", "--seed", source, "--out", packet_path,
                 "--mode", "isolated-agents", "--coordinator-id", "coordinator")
            packet = workflow.load_json(packet_path)
            paths = {}
            investigators = {}
            for role in ("business", "failure"):
                paths[role] = root / (role + ".json")
                investigators[role] = investigation(packet, role)
                paths[role].write_text(json.dumps(investigators[role]))
                call("validate-investigation", "--packet", packet_path, "--input", paths[role])
            common = ["--packet", packet_path, "--business", paths["business"],
                      "--failure", paths["failure"]]
            judge_input_path = root / "judge-input.json"
            call("assemble", *common, "--out", judge_input_path)
            judgment = case()[3]
            judgment["run_id"] = packet["run_id"]
            judgment["judge_input_digest"] = workflow.load_json(judge_input_path)["judge_input_digest"]
            judgment_path = root / "judgment.json"
            judgment_path.write_text(json.dumps(judgment))
            receipt = call("finalize", *common, "--judge", judgment_path, "--out", root / "final")
            check = subprocess.run(
                [sys.executable, str(SKILL / "scripts" / "validate-report.py"), receipt["report"]],
                capture_output=True, text=True,
            )
            self.assertEqual(check.returncode, 0, check.stderr)
            self.assertIn("Valid structure: 2 findings", check.stdout)


if __name__ == "__main__":
    unittest.main()
