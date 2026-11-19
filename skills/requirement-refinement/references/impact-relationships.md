# Impact relationships

Use this as a traversal guide, not a checklist of assumed architecture. Record
relationships established by sources or explicitly marked as inferred.

## Direction and meaning

| Relationship | Direction | Questions activated by a relevant change |
| --- | --- | --- |
| `requires` | Dependent behavior → prerequisite | Can the prerequisite change between validation and use? What if unavailable or stale? |
| `implements` | Implementation → intended requirement | Does observed behavior match the approved contract? What becomes incompatible? |
| `details` | Detailed requirement → broader requirement | Does the refinement change a guarantee or introduce a conflicting boundary? |
| `conflicts` | Rule → conflicting rule | Can both be satisfied? Under which states or cohorts? Inspect both directions. |
| `reads` | Reader → state/field | Does its interpretation assume the old meaning, unit, lifecycle, or default? |
| `writes` | Writer → state/field | Who reads this state? What partial or concurrent states become visible? |
| `emits` | Producer → event | Does payload, meaning, timing, cardinality, or delivery behavior change? |
| `consumes` | Consumer → event | Which fields and assumptions are used? How are duplicates, gaps, or older versions handled? |
| `reverses` | Reversal/compensation → forward operation | Are partial reversal, repeated reversal, expiry, and downstream side effects defined? |
| `enforces` | Guard/test/component → invariant | Can the new flow bypass it or produce a previously impossible state? |
| `exposes` | API/UI/report → behavior or state | What should clients observe during pending, failed, reversed, or stale states? |
| `configured_by` | Behavior → configuration/flag | Which cohorts differ? What happens during rollout, disablement, or rollback? |

For shared state, represent both `writer → state` and `reader → state`. Traverse
the incoming `reads` edge to find readers; do not reverse its semantic direction.
Discover event consumers through incoming `consumes` edges. Dependency IDs used
to explain impact need not form a simple directed path.

## Evidence packet

Record each dependency as:

```json
{
  "id": "D-001",
  "from": "refund.issue",
  "relationship": "reverses",
  "to": "purchase.complete",
  "basis": "supported",
  "evidence_ids": ["E-003"],
  "rationale": "The cited refund contract restores the original payment allocation."
}
```

Use `supported` when cited material establishes the relationship. Use `inferred`
for a plausible link and describe what would confirm it. Do not create a source
citation for a hypothetical subsystem. Requirement-only analysis can omit the
graph and describe conditional risks instead.

An event subscription establishes consumption, not that every payload change
affects its consumer. A test supports its scenario, not all timing/failure modes.

## Cross-boundary search

After finding directly affected behavior, inspect relevant connections:

- API callers, UI forms, validation, pending states, and cache invalidation.
- Event registrations, queue handlers, retries, dead-letter handling, and jobs.
- Database writers/readers, aggregates, indexes, migrations, exports, and reports.
- Shared authorization, tenant scopes, configuration, time, units, and rounding.
- Reversal, cancellation, deletion, expiry, restoration, and reconciliation.
- Old clients and records, feature-flag cohorts, and external consumer contracts.

Search contract names, schema fields, business synonyms, and test descriptions.
For inaccessible services, name the boundary and the needed check; do not infer
their behavior from the producer alone.

## Stopping and context budget

Maintain a frontier of unresolved consequential connections. Condense known facts
with evidence IDs before retrieving more details. Resolve high-impact branches
first. Revisit a branch if new evidence changes its relevance.

Stop when relevant branches are reviewed, or a real access, time, or context limit
prevents more work. Record unexpanded branches and why. Never equate a cluster
boundary, two-hop traversal, or empty search with proof of no further impact.
