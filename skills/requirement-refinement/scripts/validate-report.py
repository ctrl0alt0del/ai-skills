#!/usr/bin/env python3
"""Validate report structure and references using only the Python standard library."""

import argparse
import json
import re
import sys
from pathlib import Path


RELATIONSHIPS = {
    "requires", "implements", "details", "conflicts", "reads", "writes",
    "emits", "consumes", "reverses", "enforces", "exposes", "configured_by",
}
SCHEMA = {
    "schema_version": {"1.0"},
    "requirement": {"original": str, "interpretation": str, "assumptions": [str]},
    "analysis": {
        "mode": {"repository-backed", "documentation-only", "requirement-only"},
        "revision": (str, type(None)),
        "scope": [str],
    },
    "evidence": [{
        "id": str,
        "kind": {"requirement", "approved-spec", "documentation", "code", "test", "contract", "observation"},
        "location": str, "summary": str,
    }],
    "dependencies": [{
        "id": str, "from": str, "relationship": RELATIONSHIPS, "to": str,
        "basis": {"supported", "inferred"}, "evidence_ids": [str], "rationale": str,
    }],
    "findings": [{
        "id": str,
        "category": {"decision", "investigation", "regression"},
        "capability": str,
        "priority": {"blocking", "design", "follow-up"},
        "priority_reason": str, "question": str, "scenario": str,
        "affected_behaviors": [str], "impact": str,
        "evidence_ids": [str], "dependency_ids": [str],
        "basis": {"supported", "inferred", "unknown"},
        "reasoning": str, "options": [str], "suggested_owner": str,
    }],
    "settled": [{"statement": str, "evidence_ids": [str]}],
    "coverage": {
        "checked": [str], "blind_spots": [str], "unexpanded": [str], "stop_reason": str,
    },
}


def validate_report(report):
    """Return errors. An empty result means structurally valid, not proven correct."""
    errors = []

    def check(value, schema, path):
        if schema is str:
            if not isinstance(value, str) or not value.strip():
                errors.append(f"{path}: expected a non-empty string")
        elif isinstance(schema, tuple):
            if value is not None:
                check(value, str, path)
        elif isinstance(schema, set):
            if not isinstance(value, str) or value not in schema:
                errors.append(f"{path}: expected one of {', '.join(sorted(schema))}")
        elif isinstance(schema, list):
            if not isinstance(value, list):
                errors.append(f"{path}: expected an array")
            else:
                for index, item in enumerate(value):
                    check(item, schema[0], f"{path}[{index}]")
        elif isinstance(schema, dict):
            if not isinstance(value, dict):
                errors.append(f"{path}: expected an object")
                return
            for key in schema:
                if key not in value:
                    errors.append(f"{path}.{key}: missing required field")
                else:
                    check(value[key], schema[key], f"{path}.{key}")
            for key in value.keys() - schema.keys():
                errors.append(f"{path}.{key}: unexpected field")

    check(report, SCHEMA, "report")
    if errors:
        return errors

    known = {}
    for collection, prefix in [("evidence", "E"), ("dependencies", "D"), ("findings", "Q")]:
        ids = set()
        for item in report[collection]:
            identifier = item["id"]
            if not re.fullmatch(rf"{prefix}-\d{{3,}}", identifier):
                errors.append(f"{collection}: invalid ID {identifier!r}")
            if identifier in ids:
                errors.append(f"{collection}: duplicate ID {identifier}")
            ids.add(identifier)
        known[collection] = ids

    def refs(values, collection, path):
        if len(values) != len(set(values)):
            errors.append(f"{path}: duplicate references")
        for identifier in values:
            if identifier not in known[collection]:
                errors.append(f"{path}: unknown reference {identifier}")

    if not report["analysis"]["scope"]:
        errors.append("analysis.scope: include the bounded scope")
    if not report["coverage"]["checked"]:
        errors.append("coverage.checked: name at least the input that was inspected")

    for collection in ("dependencies", "findings"):
        for item in report[collection]:
            path = item["id"]
            refs(item["evidence_ids"], "evidence", f"{path}.evidence_ids")
            if item["basis"] == "supported" and not item["evidence_ids"]:
                errors.append(f"{path}: supported items require evidence")
            if collection == "findings":
                refs(item["dependency_ids"], "dependencies", f"{path}.dependency_ids")
                if not item["affected_behaviors"]:
                    errors.append(f"{path}: include affected behaviors or conditional behavior labels")
    for index, item in enumerate(report["settled"]):
        path = f"settled[{index}].evidence_ids"
        refs(item["evidence_ids"], "evidence", path)
        if not item["evidence_ids"]:
            errors.append(f"{path}: settled constraints require evidence")
    return errors


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON object key: {key}")
        result[key] = value
    return result


def reject_constant(value):
    raise ValueError(f"non-JSON numeric constant: {value}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("report", type=Path, help="Path to refinement JSON report")
    args = parser.parse_args()
    try:
        report = json.loads(
            args.report.read_text(encoding="utf-8-sig"),
            object_pairs_hook=unique_object, parse_constant=reject_constant,
        )
    except (OSError, UnicodeError, ValueError, RecursionError) as error:
        print(f"Invalid report: {error}", file=sys.stderr)
        return 1
    errors = validate_report(report)
    if errors:
        for error in errors:
            print(f"ERROR: {error}", file=sys.stderr)
        return 1
    print(f"Valid structure: {len(report['findings'])} findings, "
          f"{len(report['evidence'])} evidence entries, "
          f"{len(report['dependencies'])} dependencies.")
    print("Evidence truth, business correctness, and completeness are not verified.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
