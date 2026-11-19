# Report contract

## Markdown

Use this order, shortening sections when the user requests brevity:

1. Requirement interpretation, assumptions, analysis mode, and project revision.
2. Priority table: ID, category, capability, question/check, priority, suggested owner.
3. Details for consequential findings: scenario, affected behavior, consequence,
   evidence IDs and locations, inference, and options where useful.
4. Settled constraints to preserve, with sources.
5. Coverage: inspected areas, material blind spots, unresolved dependency frontier,
   and stopping reason. State that this is bounded analysis, not an exhaustive proof.

Group findings by capability; label decisions, investigations, and regression
checks. Derive any counts from the JSON, and keep IDs consistent across formats.
Use a sensible number of findings, not a quota. Preserve consequential findings
even when they exceed an arbitrary top-ten limit.

## JSON structure

Include every top-level key below. Use empty arrays when no entries apply. This
skeleton is illustrative; populate scope and coverage with the actual analysis.

```json
{
  "schema_version": "1.0",
  "requirement": {
    "original": "The incoming requirement, verbatim.",
    "interpretation": "A bounded working interpretation.",
    "assumptions": []
  },
  "analysis": {
    "mode": "requirement-only",
    "revision": null,
    "scope": ["Behavior described in the supplied requirement"]
  },
  "evidence": [],
  "dependencies": [],
  "findings": [],
  "settled": [],
  "coverage": {
    "checked": ["Supplied requirement"],
    "blind_spots": ["No application source or approved specification supplied"],
    "unexpanded": [],
    "stop_reason": "Available input analyzed; implementation remains unverified."
  }
}
```

Use these object shapes for array entries; all listed fields are required:

| Array | Fields |
| --- | --- |
| `evidence` | `id`, `kind`, `location`, `summary` |
| `dependencies` | `id`, `from`, `relationship`, `to`, `basis`, `evidence_ids`, `rationale` |
| `findings` | `id`, `category`, `capability`, `priority`, `priority_reason`, `question`, `scenario`, `affected_behaviors`, `impact`, `evidence_ids`, `dependency_ids`, `basis`, `reasoning`, `options`, `suggested_owner` |
| `settled` | `statement`, `evidence_ids` |

Use strings except for `evidence_ids`, `dependency_ids`, `affected_behaviors`, and
`options`, which are arrays of strings. All scope, assumptions, and coverage
arrays also contain strings. Use `revision: null` when unknown. Do not add custom
fields without updating this contract and validator.

## Values and semantics

- IDs: `E-001` for evidence, `D-001` for dependencies, `Q-001` for findings.
- Evidence kinds: `requirement`, `approved-spec`, `documentation`, `code`, `test`,
  `contract`, `observation`. Approval must be established, not inferred from a title.
- Locations: actual repository-relative path plus symbol/test/requirement ID,
  an accessible document URL and section, or `input:requirement`. Never fabricate
  files or treat illustrative source paths as inspected evidence.
- Dependency relationships: use the twelve labels in `impact-relationships.md`.
- Dependency basis: `supported` or `inferred`. Cite evidence for supported edges.
- Finding category: `decision`, `investigation`, or `regression`.
- Finding priority: `blocking`, `design`, or `follow-up`; explain the consequence
  in `priority_reason` using the definitions in SKILL.md.
- Finding basis: `supported`, `inferred`, or `unknown`. This describes support for
  the scenario's relevance, never certainty about an unsettled answer. Explain
  inferred connections in `reasoning`; use `unknown` for missing evidence.
- Use `question` for a decision question, investigation question, or concrete
  regression check. For investigations, say what evidence would resolve it.
- Use `options: []` when choices would be speculative or the item is a check.
- Suggested owner: a role such as Product, Engineering, Design, or QA.
- Settled entries require source evidence; distinguish approved policy from
  observed implementation in the statement itself.

Cross-reference only IDs present in the same report. Findings may reference zero
dependencies when analyzing wording alone. Supported findings require at least
one evidence reference; inferred findings can cite the input as their premise.
In requirement-only mode, identify hypothetical features conditionally.

Run `scripts/validate-report.py` on saved JSON. It checks fields, value types,
allowed labels, unique IDs, and references; it cannot verify source truth or
detect every omitted dependency.
