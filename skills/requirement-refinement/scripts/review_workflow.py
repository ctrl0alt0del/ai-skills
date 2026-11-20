#!/usr/bin/env python3
"""Bind two investigations and a judge review to one frozen refinement packet.

This tool validates artifacts; the host launches agents. Recorded actor IDs and
source checks are declarations, not proof of agent isolation or evidence truth.
Python 3.10+; standard library only.
"""

import argparse
import copy
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import sys
import uuid

sys.dont_write_bytecode = True
_spec = importlib.util.spec_from_file_location(
    "_refinement_report_validator", Path(__file__).with_name("validate-report.py")
)
if _spec is None or _spec.loader is None:
    raise RuntimeError("Cannot load the bundled report validator.")
_report = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_report)

MODES = {"isolated-agents", "sequential-fallback"}
ROLES = {"business", "failure"}
ACTIONS = {"accept", "revise", "merge", "reject"}
CHECK_RESULTS = {"reopened", "packet-only", "unavailable"}
NOTICE = (
    "Structure, lineage, and declared actor identities checked. Actual agent "
    "isolation, evidence truth, business correctness, and completeness are not verified."
)


class WorkflowError(ValueError):
    """A review artifact violates the workflow contract."""


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False, allow_nan=False).encode("utf-8")


def digest(value):
    return hashlib.sha256(canonical(value)).hexdigest()


def load_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8-sig"),
                      object_pairs_hook=_report.unique_object,
                      parse_constant=_report.reject_constant)


def fields(value, expected, label):
    if not isinstance(value, dict):
        raise WorkflowError(f"{label}: expected an object.")
    missing, extra = set(expected) - value.keys(), value.keys() - set(expected)
    if missing or extra:
        details = []
        if missing:
            details.append("missing " + ", ".join(sorted(missing)))
        if extra:
            details.append("unexpected " + ", ".join(sorted(extra)))
        raise WorkflowError(f"{label}: {'; '.join(details)}.")


def text(value, label):
    if not isinstance(value, str) or not value.strip():
        raise WorkflowError(f"{label}: expected a non-empty string.")
    return value


def identity(value, label):
    text(value, label)
    if value != value.strip():
        raise WorkflowError(f"{label}: remove leading/trailing whitespace.")
    return value


def array(value, label):
    if not isinstance(value, list):
        raise WorkflowError(f"{label}: expected an array.")
    return value


def choice(value, allowed, label):
    if not isinstance(value, str) or value not in allowed:
        raise WorkflowError(f"{label}: expected one of {', '.join(sorted(allowed))}.")


def sha(value, label):
    if not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{64}", value):
        raise WorkflowError(f"{label}: expected a lowercase SHA256 digest.")


def report(value, label, context=None):
    errors = _report.validate_report(value)
    if errors:
        raise WorkflowError(f"{label}: " + "; ".join(errors))
    if context is not None:
        for key in ("requirement", "analysis"):
            if value[key] != context[key]:
                raise WorkflowError(
                    f"{label}.{key}: differs from the frozen packet. Preserve the "
                    "framing, or prepare a new packet and rerun both investigators."
                )


def validate_packet(packet):
    fields(packet, {"schema_version", "run_id", "execution", "context", "packet_digest"},
           "packet")
    choice(packet["schema_version"], {"refinement-review-packet/1"}, "packet.schema_version")
    identity(packet["run_id"], "packet.run_id")
    try:
        uuid.UUID(packet["run_id"])
    except ValueError as error:
        raise WorkflowError("packet.run_id: expected a UUID.") from error
    execution = packet["execution"]
    fields(execution, {"mode", "coordinator_id", "fallback_reason"}, "packet.execution")
    choice(execution["mode"], MODES, "packet.execution.mode")
    identity(execution["coordinator_id"], "packet.execution.coordinator_id")
    if execution["mode"] == "sequential-fallback":
        text(execution["fallback_reason"], "packet.execution.fallback_reason")
    elif execution["fallback_reason"] is not None:
        raise WorkflowError("packet.execution.fallback_reason: must be null for isolated agents.")
    report(packet["context"], "packet.context")
    if packet["context"]["findings"]:
        raise WorkflowError("packet.context.findings: must be empty before independent analysis.")
    sha(packet["packet_digest"], "packet.packet_digest")
    if packet["packet_digest"] != digest({k: v for k, v in packet.items()
                                          if k != "packet_digest"}):
        raise WorkflowError("packet.packet_digest: content changed; prepare a new packet.")


def prepare_packet(seed, mode, coordinator_id, fallback_reason=None):
    packet = {
        "schema_version": "refinement-review-packet/1",
        "run_id": str(uuid.uuid4()),
        "execution": {
            "mode": mode, "coordinator_id": coordinator_id,
            "fallback_reason": fallback_reason,
        },
        "context": copy.deepcopy(seed),
    }
    packet["packet_digest"] = digest(packet)
    validate_packet(packet)
    return packet


def validate_investigation(packet, investigation, expected_role=None):
    validate_packet(packet)
    fields(investigation, {"schema_version", "run_id", "packet_digest", "role",
                           "agent_id", "report"}, "investigation")
    choice(investigation["schema_version"], {"refinement-investigation/1"},
           "investigation.schema_version")
    choice(investigation["role"], ROLES, "investigation.role")
    if expected_role is not None and investigation["role"] != expected_role:
        raise WorkflowError(f"investigation.role: expected {expected_role}, got "
                            f"{investigation['role']}.")
    if (investigation["run_id"] != packet["run_id"]
            or investigation["packet_digest"] != packet["packet_digest"]):
        raise WorkflowError("investigation: stale or different packet/run; rerun this investigator.")
    actor = identity(investigation["agent_id"], "investigation.agent_id")
    if (packet["execution"]["mode"] == "isolated-agents"
            and actor == packet["execution"]["coordinator_id"]):
        raise WorkflowError("investigation.agent_id: an isolated investigator must be "
                            "different from the coordinator.")
    report(investigation["report"], "investigation.report", packet["context"])


def assemble(packet, business, failure):
    validate_investigation(packet, business, "business")
    validate_investigation(packet, failure, "failure")
    if (packet["execution"]["mode"] == "isolated-agents"
            and business["agent_id"] == failure["agent_id"]):
        raise WorkflowError("investigations: isolated business and failure agents "
                            "must have different IDs.")
    result = {
        "schema_version": "refinement-judge-input/1",
        "packet": copy.deepcopy(packet),
        "investigations": {"business": copy.deepcopy(business), "failure": copy.deepcopy(failure)},
        "input_digests": {"business": digest(business), "failure": digest(failure)},
    }
    result["judge_input_digest"] = digest(result)
    return result


def validate_judgment(packet, business, failure, judgment):
    judge_input = assemble(packet, business, failure)
    fields(judgment, {"schema_version", "run_id", "judge_input_digest", "agent_id",
                      "report", "verdicts", "added_findings", "source_checks"}, "judgment")
    choice(judgment["schema_version"], {"refinement-judgment/1"}, "judgment.schema_version")
    if (judgment["run_id"] != packet["run_id"]
            or judgment["judge_input_digest"] != judge_input["judge_input_digest"]):
        raise WorkflowError("judgment: stale or different inputs; assemble current inputs "
                            "and obtain a fresh judge review.")
    actor = identity(judgment["agent_id"], "judgment.agent_id")
    other_actors = {packet["execution"]["coordinator_id"],
                    business["agent_id"], failure["agent_id"]}
    if packet["execution"]["mode"] == "isolated-agents" and actor in other_actors:
        raise WorkflowError("judgment.agent_id: the isolated judge must be different "
                            "from both investigators and the coordinator.")
    report(judgment["report"], "judgment.report", packet["context"])
    final_ids = {finding["id"] for finding in judgment["report"]["findings"]}
    candidates = {
        f"{role}:{finding['id']}"
        for role, investigation in (("business", business), ("failure", failure))
        for finding in investigation["report"]["findings"]
    }
    seen, targets, mapping_sizes = set(), {}, {}
    for index, verdict in enumerate(array(judgment["verdicts"], "judgment.verdicts")):
        label = f"judgment.verdicts[{index}]"
        fields(verdict, {"candidate_id", "action", "finding_ids", "rationale"}, label)
        candidate = text(verdict["candidate_id"], f"{label}.candidate_id")
        if candidate not in candidates:
            raise WorkflowError(f"{label}: unknown candidate {candidate!r}.")
        if candidate in seen:
            raise WorkflowError(f"{label}: duplicate verdict for {candidate}.")
        seen.add(candidate)
        choice(verdict["action"], ACTIONS, f"{label}.action")
        text(verdict["rationale"], f"{label}.rationale")
        mapped = array(verdict["finding_ids"], f"{label}.finding_ids")
        for identifier in mapped:
            text(identifier, f"{label}.finding_ids")
            if identifier not in final_ids:
                raise WorkflowError(f"{label}: unknown final finding {identifier!r}.")
        if len(mapped) != len(set(mapped)):
            raise WorkflowError(f"{label}: duplicate final finding references.")
        if verdict["action"] == "reject":
            if mapped:
                raise WorkflowError(f"{label}: a rejected candidate must map to no final findings.")
        elif not mapped:
            raise WorkflowError(f"{label}: a non-rejected candidate must map to final findings.")
        if verdict["action"] == "accept" and len(mapped) != 1:
            raise WorkflowError(f"{label}: accept must map to exactly one nonshared final finding; "
                                "use revise when splitting a candidate.")
        mapping_sizes[candidate] = len(mapped)
        for identifier in mapped:
            targets.setdefault(identifier, []).append((candidate, verdict["action"]))
    if candidates != seen:
        raise WorkflowError("judgment.verdicts: missing candidates: "
                            + ", ".join(sorted(candidates - seen)))
    for identifier, origins in targets.items():
        if len(origins) < 2 and any(action == "merge" for _, action in origins):
            raise WorkflowError(f"judgment.verdicts: merge target {identifier} needs at "
                                "least two source candidates.")
        if len(origins) > 1:
            for candidate, action in origins:
                if action != "merge" and not (
                    action == "revise" and mapping_sizes[candidate] > 1
                ):
                    raise WorkflowError(
                        f"judgment.verdicts: {candidate} shares final finding {identifier}; "
                        "use merge, or revise only when this candidate also splits across "
                        "multiple final findings."
                    )
    added = set()
    for index, item in enumerate(array(judgment["added_findings"], "judgment.added_findings")):
        label = f"judgment.added_findings[{index}]"
        fields(item, {"finding_id", "rationale"}, label)
        identifier = text(item["finding_id"], f"{label}.finding_id")
        text(item["rationale"], f"{label}.rationale")
        if identifier not in final_ids or identifier in added or identifier in targets:
            raise WorkflowError(f"{label}: added finding must be an existing final ID, "
                                "listed once, and not mapped from an input candidate.")
        added.add(identifier)
    missing = final_ids - targets.keys() - added
    if missing:
        raise WorkflowError("judgment: final findings have no candidate mapping or "
                            "added-findings rationale: " + ", ".join(sorted(missing)))
    checked = set()
    for index, check in enumerate(array(judgment["source_checks"], "judgment.source_checks")):
        label = f"judgment.source_checks[{index}]"
        fields(check, {"location", "result", "note"}, label)
        location = text(check["location"], f"{label}.location")
        choice(check["result"], CHECK_RESULTS, f"{label}.result")
        text(check["note"], f"{label}.note")
        if location in checked:
            raise WorkflowError(f"{label}: duplicate location; combine its check notes.")
        checked.add(location)
    required = {item["location"] for item in judgment["report"]["evidence"]}
    if required - checked:
        raise WorkflowError("judgment.source_checks: missing final evidence locations: "
                            + ", ".join(sorted(required - checked)))
    return judge_input


def write_new_json(path, value):
    payload = json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False,
                         allow_nan=False) + "\n"
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    stream = path.open("x", encoding="utf-8", newline="\n")
    created = None
    try:
        with stream:
            created = os.fstat(stream.fileno())
            stream.write(payload)
    except BaseException:
        # The exclusive open above must succeed before cleanup becomes possible.
        # Check identity as well so a concurrent replacement is not removed.
        if created is not None:
            try:
                current = path.stat(follow_symlinks=False)
                if (current.st_dev, current.st_ino) == (created.st_dev, created.st_ino):
                    path.unlink()
            except OSError:
                pass
        raise


def finalize(packet, business, failure, judgment, out):
    judge_input = validate_judgment(packet, business, failure, judgment)
    audit = {
        "schema_version": "refinement-review/1",
        "run_id": packet["run_id"],
        "execution": copy.deepcopy(packet["execution"]),
        "packet_digest": packet["packet_digest"],
        "input_digests": judge_input["input_digests"],
        "judge_input_digest": judge_input["judge_input_digest"],
        "judgment_digest": digest(judgment),
        "report_digest": digest(judgment["report"]),
        "packet": copy.deepcopy(packet),
        "investigations": judge_input["investigations"],
        "judgment": copy.deepcopy(judgment),
    }
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.mkdir()  # Exclusive reservation: never replace an existing destination.
    written = []
    try:
        for name, content in (("refinement.json", judgment["report"]), ("review.json", audit)):
            target = out / name
            write_new_json(target, content)
            written.append(target)
    except Exception:
        for target in written:
            target.unlink()
        try:
            out.rmdir()
        except OSError:
            pass
        raise
    return {
        "run_id": packet["run_id"], "execution": packet["execution"],
        "report": str(out / "refinement.json"), "review": str(out / "review.json"),
        "findings": len(judgment["report"]["findings"]),
        "candidates": len(judgment["verdicts"]), "report_digest": audit["report_digest"],
        "notice": NOTICE,
    }


def parser():
    root = argparse.ArgumentParser(description=__doc__)
    commands = root.add_subparsers(dest="command", required=True)
    prepare = commands.add_parser("prepare", help="Freeze a finding-free report as shared context")
    prepare.add_argument("--seed", required=True, type=Path)
    prepare.add_argument("--out", required=True, type=Path)
    prepare.add_argument("--mode", required=True, choices=sorted(MODES))
    prepare.add_argument("--coordinator-id", required=True)
    prepare.add_argument("--fallback-reason")
    validate = commands.add_parser("validate-investigation", help="Validate one investigator artifact")
    validate.add_argument("--packet", required=True, type=Path)
    validate.add_argument("--input", required=True, type=Path)
    for name in ("assemble", "finalize"):
        command = commands.add_parser(name)
        for flag in ("packet", "business", "failure", "out"):
            command.add_argument("--" + flag, required=True, type=Path)
        if name == "finalize":
            command.add_argument("--judge", required=True, type=Path)
    return root


def main(argv=None):
    args = parser().parse_args(argv)
    try:
        if args.command == "prepare":
            packet = prepare_packet(load_json(args.seed), args.mode, args.coordinator_id,
                                    args.fallback_reason)
            write_new_json(args.out, packet)
            result = {"packet": str(args.out), "run_id": packet["run_id"],
                      "packet_digest": packet["packet_digest"], "execution": packet["execution"]}
        elif args.command == "validate-investigation":
            packet, investigation = load_json(args.packet), load_json(args.input)
            validate_investigation(packet, investigation)
            result = {"valid": True, "role": investigation["role"],
                      "findings": len(investigation["report"]["findings"]), "notice": NOTICE}
        else:
            packet, business, failure = (load_json(getattr(args, key))
                                         for key in ("packet", "business", "failure"))
            if args.command == "assemble":
                judge_input = assemble(packet, business, failure)
                write_new_json(args.out, judge_input)
                result = {"judge_input": str(args.out),
                          "judge_input_digest": judge_input["judge_input_digest"],
                          "candidates": sum(len(x["report"]["findings"]) for x in (business, failure)),
                          "notice": NOTICE}
            else:
                result = finalize(packet, business, failure, load_json(args.judge), args.out)
        print(json.dumps(result, indent=2, ensure_ascii=False, allow_nan=False))
        return 0
    except (OSError, UnicodeError, ValueError, RecursionError) as error:
        print(f"Review workflow error: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
