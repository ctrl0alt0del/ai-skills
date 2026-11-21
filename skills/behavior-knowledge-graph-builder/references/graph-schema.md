# Behavior graph schema

Validate proposed graph artifacts with `scripts/graph_schema.py` through the builder CLI.
The schema describes graph content, independently of review sessions and storage.

## Graph input

Use UTF-8 JSON with schema_version `behavior-graph/1` and these required keys:

```json
{
  "schema_version": "behavior-graph/1",
  "project": {"name": "Example", "scope": ["A bounded business flow"], "sources": []},
  "nodes": [],
  "dependencies": [],
  "evidence": [],
  "coverage": {"checked": [], "blind_spots": [], "unexpanded": [], "conflicts": []}
}
```

This is an empty initialization example; a reviewable proposal requires at least
one node and a nonempty coverage.checked. ID strings must match
`[a-z0-9][a-z0-9._-]{0,119}`. Keep IDs stable across rounds. No path separators.

- project.sources entries: `id`, `kind` (repository/document/conversation),
  `location`, `revision` (string or null). Record actual inspected versions.
- nodes: `id`, `kind`, `title`, `summary`, `domain`, `aliases` (string array),
  `owner_role` (string or null), `claims` (array).
- Node kinds: behavior, state, event, rule, requirement, interface, configuration.
- claims: `id`, `aspect`, `perspective`, `basis`, `statement`, `evidence_ids`.
- Claim aspects: actor, trigger, precondition, state_transition, outcome, invariant,
  timing, failure, recovery, assumption, other.
- Perspectives: intended, observed, proposed. Basis: supported, inferred, unknown.
- dependencies: `id`, `from`, `relationship`, `to`, `basis`, `evidence_ids`,
  `rationale`, `conditions` (string array).
- Relationship labels: requires, implements, details, conflicts, reads, writes,
  emits, consumes, reverses, enforces, exposes, configured_by.
- Dependency basis: supported or inferred.
- evidence: `id`, `kind`, `location`, `source_id`, `summary`.
- Evidence kinds: requirement, approved-spec, documentation, code, test, contract,
  observation, human-feedback. Evidence source_id must resolve to project.sources.
- All evidence_ids resolve to evidence entries. Every supported claim/edge has
  evidence. Every dependency endpoint resolves to a node. IDs are unique within
  each record class, with claim IDs unique across all nodes. All coverage arrays
  contain strings. A user's statement is evidence of intended policy or reported
  behavior, not proof that code implements it.
