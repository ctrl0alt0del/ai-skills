# Builder handoff and multi-prompt continuation

Use `scripts/review_session.py` to hand immutable graph/feedback inputs to
`behavior-knowledge-graph-builder` and safely apply its returned draft. The helper
does not invoke a model, poll indefinitely, resolve feedback, or approve a graph.

## Contents

- Prepare one bounded request
- Invoke the builder and apply its result
- Resume the same session in another prompt

## Prepare one bounded request

Choose exactly one existing session, saved connection, or server config. For an
initialized local session, including an empty session before its first graph:

```bash
python3 scripts/review_session.py prepare --session /project/review --mode create --scope "Order cancellation" --source /project/source --work /project/review-work/round-1
```

For submitted review feedback on a configured graph:

```bash
python3 scripts/review_session.py prepare --config /project/config.json --graph-id orders --mode review --scope "Order cancellation" --work /project/review-work/round-2
```

For code changes, pass `--mode code_change --changes /project/changes.json`.
Use `mixed` when handling source changes and selected feedback together. The
changes file is an array of `{source_id, location, from_revision, to_revision,
paths, summary?}`; revisions may be null when unknown. Repeat `--scope` or `--source`
for additional bounded scopes/locations. Existing graph source locations and
declared change locations supply the default sources.

Review/mixed/create modes select all pending feedback by default. Repeat
`--feedback-id NOTE_ID` to select an explicit pending subset. Code-change mode
does not consume human feedback; use mixed mode to handle both. Pending deleted
notes remain selectable. Review mode requires feedback, code-change mode requires
a declared change, and mixed mode requires both. The helper checks that the graph,
review, and feedback snapshots agree at the exact review version.

Create mode also supports a fresh configured document target when both graph and
review are absent. It checks publishing/initialization capabilities and records
`base: null`; the actual destination ID stays in the app-only checkpoint. It does
not silently create a parallel local session. Existing graph content without its
review requires explicit initialization/reconciliation first.

The work directory must be new. Preparation creates:

- `request.json`: immutable `behavior-graph-build-request/1` input containing the
  scope, sources, exact base graph/tokens, selected feedback, and declared changes.
- `checkpoint.json`: app-only locator, destination ID, request digest, and phase.

Keep work files in the project review workspace. Pass only the builder request
and authorized source access to the builder. Keep repository credentials and the
app checkpoint out of that request. Resolve the builder by skill name or a supplied
installation path; do not hardcode an internal installed-skill directory ID.

## Invoke the builder and apply its result

Invoke `behavior-knowledge-graph-builder` with `request.json` and a draft output
directory. It returns a `behavior-graph-build-result/1` packet with the graph,
request digest/base, derived change IDs, summary, questions, and exact per-note
recommendations. A subagent is useful when available; the current agent may load
and follow the builder skill when separate execution is unavailable. Reading a
skill alone is not evidence that another model invocation occurred.

```bash
python3 scripts/review_session.py apply-result --work /project/review-work/round-2 --result /project/review-work/round-2/builder-result.json
```

Apply validates the result and rereads authoritative storage. Any changed graph
identity, graph revision/digest, review version, or selected feedback snapshot
rejects the old result. Reconcile and prepare a fresh request rather than changing
its saved base tokens. The helper publishes a changed graph through the existing
proposal operation. An unchanged graph creates no extra round and does not alter
the review version. A fresh document target uses a request-bound immutable
revision and explicit review initialization without overwriting an existing review.

Application retains `result.json` and a publication receipt in the app checkpoint.
It deliberately leaves discussion resolution, processing receipts, approval, and
final export to the coordinator. Inspect the recommendations and fresh feedback,
then use the existing `graph_review.py` commands with current expected versions.
Record outcomes only after their underlying work is saved. A new proposal may
automatically reopen notes and increment their feedback revisions; never replace
the result's inspected revision number with a newer one just to acknowledge it.
Open clarification questions remain open even when their revision is processed.

## Resume the same session in another prompt

Keep the work-directory path in the conversation handoff and run:

```bash
python3 scripts/review_session.py status --work /project/review-work/round-2
```

`check-resume` is an alias. The command loads the saved locator and reports the
current pinned graph, authoritative review, pending feedback, publication receipt,
and next action. It does not infer current state from the old request or a server
process. The app may be restarted independently using the same connection/config.

| Checkpoint phase | Continue with |
| --- | --- |
| `prepared` | Build/apply if the base is still current; otherwise prepare a new request after reconciliation |
| `applying` | Inspect whether an apply is still running or was interrupted; no automatic re-apply |
| `published` | Inspect result recommendations and fresh feedback; repeat apply of the identical result only returns the existing publication |
| `needs-reconciliation` | Inspect authoritative storage and partial publication before preparing another request |

Persist `applying` before changing repository data. A crash after publication but
before the completed checkpoint can leave an ambiguous outcome; an exception can
also leave an immutable graph without its review reference. Status exposes such
partial storage, and apply refuses to repeat that request. Reconcile explicitly:
inspect the graph digest/revision and review/audit, retain successful work, and
create the next request from the actual current state. Do not manufacture a
completion checkpoint or approval to bypass uncertainty.

After feedback processing, present the revised graph or specific remaining
question. Save the current work path, locator, selected graph ID, outstanding
questions, and next action in the handoff. The next prompt resumes the same review;
it does not initialize another one. Human approval and the existing final-export
gates remain separate from every builder result and operational checkpoint.
