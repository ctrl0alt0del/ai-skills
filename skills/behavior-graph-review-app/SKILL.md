---
name: behavior-graph-review-app
description: >-
  Set up, launch, maintain, and resume the packaged behavior graph review app.
  Coordinate review sessions across prompts by invoking the behavior knowledge
  graph builder for new graphs, human feedback, and code changes, then persist
  proposals and exact feedback outcomes. Use for opening the graph UI, configuring
  repositories, continuing review, recording approval, or maintaining the app.
---

# Behavior Graph Review App

Own the review application and the conversation that coordinates it. Delegate
business-graph investigation and construction to `behavior-knowledge-graph-builder`.
Keep one durable review across prompts, with the repositories as its authority.

## Resources and boundaries

- Read [repository-config.md](references/repository-config.md) when configuring
  storage; read [repository-contract.md](references/repository-contract.md) for
  consistency, annotations, feedback receipts, and connection semantics.
- Read [builder-handoff.md](references/builder-handoff.md) before preparing or
  applying a builder request; use its exact request/result and checkpoint contract.
- Read [runtime-contract.md](references/runtime-contract.md) for CLI/API actions,
  approval, and final artifacts; read [chat-review.md](references/chat-review.md)
  when feedback or approval comes through chat.
- Use `scripts/graph_review.py` for the released runtime and repository actions;
  use `scripts/review_session.py` for durable builder handoffs and resumption.
- Launch `assets/review-app/dist/`. Normal review never generates a frontend,
  installs Node packages, or rebuilds the app. Missing/invalid dist is a packaging
  problem: report it and continue the same review in chat.
- Discover the builder by its skill name from available skills or the personal
  skill catalog. Read its instructions and invoke it in a delegated task when
  available, or follow that skill in the current turn. Do not hardcode an installed
  skill ID or claim the Python helper itself invokes an AI model.

## 1. Set up or resume the application

Infer the project and bounded review scope. Reuse its existing connection/config,
selected graph, and saved work checkpoint. Read current review and pending
feedback before deciding what this prompt requires. Preserve explicit graph IDs;
never choose the first catalog item silently or create a duplicate session merely
because the conversation or review modality changed.

For a new default local review, create a dedicated project directory, outside the
installed skill, then launch the shipped app before lengthy graph investigation:

```bash
python3 /absolute/app/scripts/graph_review.py init --session /project/.behavior-graph-review/orders --title "Order behavior review"
python3 /absolute/app/scripts/graph_review.py serve --connection /project/.behavior-graph-review/orders/connection.json --port 0
```

Honor supplied `config.json` bindings. Use the same config or saved connection for
server and agent commands. The configuration guide covers existing sessions,
filesystem, SQLite, S3, and HTTP, and explicit registration/import. Do not move data
by changing bindings or improvise a missing adapter during ordinary graph review.

Use the host's long-running process facility, capture the real URL/process handle,
and verify health. Reuse a healthy server; restart against the same data if needed.
Provide a supported local/private preview only when reachable. If the environment
cannot expose it, say so and use chat without creating another review. Do not
expose private project data publicly or imply the server survives its host.

## 2. Prepare one builder request

For an existing review, read its exact immutable graph revision. Creation can
start from an initialized empty local session or an explicitly selected absent
document graph/review with a null base, as the helper supports. Do not invent a
baseline or create a parallel session to bypass configured storage. Choose `create`,
`review`, `code_change`, or `mixed` based on this prompt and submitted feedback.
Record genuine chat corrections in the same ledger before preparing the request.
Use the documented changes-file format for code changes, with actual baseline and
target revisions or a declared reinspection scope.

```bash
python3 /absolute/app/scripts/review_session.py prepare --connection /project/.behavior-graph-review/orders/connection.json --mode review --scope "Order cancellation and notifications" --work /project/.behavior-graph-review/orders/build-2
```

Use a fresh work directory for each request. Pass its immutable `request.json`,
source access, and output location to the builder. Include exact feedback
snapshots/revisions and source changes; keep storage credentials and the app-only
checkpoint out of the builder context. For first-use/provisioning details and
optional `--changes`, follow `builder-handoff.md`.

Invoke the builder to produce `proposal.json`, its report, and `result.json`.
The builder returns recommendations; it does not mutate the live review or approve
anything. Continue in this turn when the work is authorized; do not add a separate
permission step merely to invoke the builder or save a draft proposal.

## 3. Apply the graph, then handle feedback

```bash
python3 /absolute/app/scripts/review_session.py apply-result --work /project/.behavior-graph-review/orders/build-2 --result /project/.behavior-graph-review/orders/build-2/result.json
```

The helper checks request/base identity and current review version, publishes a
changed draft through the existing repository operation, and records continuation
state. An unchanged graph needs no extra round. A stale result requires re-reading
and reconciling current input; do not substitute a new version into an old result.
The helper does not resolve annotations, acknowledge feedback, approve, or export.

Read the review again after publication: changing a target can reopen a settled
note and advance its feedback revision. Before either resolving or acknowledging
a note, check that its exact revision and relevant context still match what the
builder inspected. If they changed, preserve its disposition and receipt and
prepare fresh input; a newer state_version alone does not make the old answer
applicable. Apply each justified recommended
resolution with `graph_review.py resolve --source agent`, preserving its explanation.
Use `accepted_gap` only for actual human acceptance; keep `needs_clarification`
questions open. Save answers or decisions before recording processing receipts.

Acknowledge only the exact annotation revision the builder handled, using
`mark-processed` and the latest actual review version. If that annotation changed,
leave the new revision pending and prepare another request. Include deletion
tombstones; preserve the audit. Processing, discussion resolution, and human
approval remain separate. Never acknowledge unsaved work or hide disagreement.

When all questions in a submitted batch are settled, use `complete-review` to
clear its readiness signal without forcing a new graph round. Intermediate saved
notes alone need not mean the reviewer finished. Present the changed graph, the
remaining questions, and concise UI/chat review instructions.

## 4. Continue across prompts

Keep the connection/config path, graph ID, current work directory, applied result,
remaining questions, next action, and server availability in a durable handoff.
The work checkpoint is operational metadata; re-read repository state on resume:

```bash
python3 /absolute/app/scripts/review_session.py status --work /project/.behavior-graph-review/orders/build-2
```

Resume completed publication from its checkpoint instead of creating another
round. Inspect current graph/history and receipts after interruptions. An
ambiguous publication must be reconciled before a new attempt; do not blindly
repeat it. For a new code-change or review request, create a new work directory
from the current pinned graph. New prompts can supply code changes, UI feedback,
chat decisions, status requests, or approval without resetting the session.

While the turn is active, bounded `wait` or `pending-feedback --after-version N
--timeout 20` calls can collect feedback. Communicate progress and stop after a few
idle waits; record the next action and return to the user. The UI and Python server
do not run a background AI. Retain processed-but-open questions in the agenda.

## 5. Record approval and finalize

Wait for the user's explicit approval of the exact settled whole proposal. Read
current state; never click approval for the user or invent a chat approval. Record
an actual chat approval through `approve-chat` with its real source reference.
New semantic feedback or a changed graph requires fresh approval.

Use the runtime's guarded `export` only for a supported backend and fresh output
directory. Current final-bundle export is local-session-only; do not work around a
missing remote capability with stale data or fabricated approval. Verify its
receipt and manifest, then explain how refinement can consume the graph. Preserve
the review session for future code changes and feedback.

## Explicit app-maintenance requests

When asked to change the app, own its UI source, server, repository adapters,
configuration, handoff runtime, and packaging in the source repository. Read its
project instructions, make the requested change, run relevant checks, rebuild the
release dist, and update app contracts. Keep graph reasoning in the builder.
The maintained source is `apps/behavior-graph-review/` in the
[ai-skills repository](https://github.com/ctrl0alt0del/ai-skills); its default build
output is `skills/behavior-graph-review-app/assets/review-app/dist`.
Maintain identical copies of the small pure schema/handoff contracts where both
skills need them; use the parity checks. Do not modify application source being
mapped as an incidental part of a graph-review session.
