# Review runtime contract

## Contents

- Graph input
- Review state
- Packaged app and repositories
- HTTP interface
- Command interface
- Finalization

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

## Review state

The bundled local adapter persists sessions in a user-specified directory,
separately from final graph artifacts, using SQLite transactions for cross-process
access. CLI and browser use the same store; no browser-only feedback. Other
adapters implement the guarantees in [repository-contract.md](repository-contract.md).
The local CLI state has:

- session_id, title, state_version (integer optimistic lock), round (0 before a
  proposal), graph_digest, graph (null initially), annotations, rounds, approval,
  review_requested (boolean), finalized (null or receipt).
- State reads also expose agent_active from a short-lived runtime heartbeat.
  Presence is not graph/review content: it must not change state_version, approval,
  the review digest, or audit semantics. It indicates an active agent wait in the
  current turn, not an autonomous model running inside the server.
- annotations: id, target_type (graph/node/dependency/claim), target_id (null for
  graph), kind (question/correction/missing/confirmation/note), text, source
  (ui/chat/agent), source_ref (string or null), round, created_at, status
  (open/resolved/deleted), resolution (null or {disposition,text,source,round,at}).
  Edits/deletions add update/deletion metadata while preserving the original
  source and creation metadata. Existing notes need no migration.
- Resolution dispositions: addressed, accepted_gap, rejected. Require a meaningful
  explanation. Deletion is an audited tombstone, never erasure of history.
  Deleted notes do not block settlement or reopen on later proposals. Editing a
  resolved note reopens it. Orphaned active targets remain visible and unresolved
  unless explicitly dispositioned or deleted by a reviewer.
- rounds: round, digest, summary, created_at, node_count, dependency_count. Preserve
  complete snapshots in storage. approval contains round, digest, source, text,
  created_at, acknowledge_limits. Source is ui or chat, never agent.
- Every proposal or feedback mutation increments state_version and clears approval
  and finalized. Proposal increments round, resets review_requested, preserves
  annotations, and records a human-readable change summary.
- Reopen previously resolved annotations when their referenced node, dependency,
  or claim changes or disappears in a new proposal. Reopen graph-level resolved
  annotations when the graph changes. Preserve previous resolutions in audit
  history. Do not reopen them for an identical graph/no-change round.
- Approval requires a nonempty valid proposal, no open annotations,
  review_requested=false, and acknowledge_limits=true. Approval is tied to exact
  round, digest and the transaction's state, not a generic prior 'yes'.

## Packaged app and repositories

App source lives in `apps/behavior-graph-review/` in the development repository.
The installed skill contains the built `assets/review-app/dist/` directory,
including an HTML entry, content-hashed assets, and a SHA-256 manifest. Serve this
distribution without installing Node packages, compiling, or generating UI at
runtime. Missing/invalid assets fail with a packaging error.

The app uses injected `GraphRepository` and `HumanReviewRepository` interfaces.
The default HTTP adapters are separate from rendering. Server-side protocols and
local adapters live in `scripts/graph_repositories.py`. Review snapshots exclude
graph content and reference `graph_id`, `graph_revision`, and `graph_digest`; load
that exact graph snapshot to obtain a consistent view. See the repository contract
for adapter configuration, immutable revisions, and conditional-write guarantees.

## HTTP interface

Use a Python standard-library server, loopback binding by default, no external
assets/dependencies. Serve only the packaged viewer and named API routes; do not
serve arbitrary filesystem paths. Limit request sizes. Enforce Host/Origin checks
for local operation and JSON-only mutations with a server CSRF token; no CORS.
Do not log annotation bodies or auth tokens. Remote preview is optional and needs
an explicitly configured origin/proxy; do not bind publicly by default.

- GET `/` serves the prebuilt `index.html`; other static routes are limited to
  the verified distribution manifest. JavaScript is external, with same-origin CSP.
- GET `/api/bootstrap` returns `{csrf_token}` for the HTTP adapters.
- GET `/api/graphs` returns `{graphs:[...]}` with available graph summaries.
- GET `/api/graphs/{graph_id}?revision=R` returns
  `{id,title,revision,digest,graph}`. Omit the revision for latest. Treat IDs and
  revisions as opaque values and URL-encode them. Local revisions are round strings.
- GET `/api/graphs/{graph_id}/review` returns the review snapshot without `graph`,
  with its exact `graph_id` and `graph_revision` references. Each `rounds` entry
  also includes `graph_revision` and `digest`; never infer a storage revision from
  the displayed round number.
- GET `/api/graphs/{graph_id}/annotations` returns `{annotations:[...]}`;
  GET `/api/graphs/{graph_id}/annotations/{id}` returns one annotation.
- POST `/api/graphs/{graph_id}/annotations` body: expected_version, target_type,
  target_id, kind, text. The server records source as ui.
- PATCH `/api/graphs/{graph_id}/annotations/{id}` body: expected_version and
  text and/or kind. Preserve target and creation metadata; reopen a resolved note.
- DELETE `/api/graphs/{graph_id}/annotations/{id}` JSON body: expected_version.
  Record a tombstone and audit event; invalidate prior approval.
- POST `/api/graphs/{graph_id}/resolve` body: expected_version, annotation_id,
  disposition, text. POST `.../reopen` body: expected_version, annotation_id.
- POST `/api/graphs/{graph_id}/request-review` body: expected_version. Set
  review_requested and invalidate approval.
- POST `/api/graphs/{graph_id}/approve` body: expected_version,
  acknowledge_limits:true, text. Record approval; do not export final files.
- Mutations return the complete updated review snapshot, without graph content.
  POST, PATCH, and DELETE require `Content-Type: application/json`, a trusted Origin,
  and `X-Review-Token` from bootstrap. Stale versions return 409; malformed input
  returns 400; forbidden requests 403; absent IDs/revisions 404.
  Error JSON is `{error:"message"}`.
- GET `/api/health` reports server health. Legacy single-session state/round and
  action routes remain available for existing clients of `serve --session`.
- The frontend polls review state every few seconds and preserves in-progress user text.
  It also notices presence changes without a review-version change. When an agent
  is connected, it says feedback is saved for that agent; otherwise it gives the
  return-to-chat fallback. It cannot invoke an LLM or promise an independent agent.

## Command interface

Entry point: scripts/graph_review.py. Use Python 3.10+ standard library only.

- `init --session PATH --title TEXT`: create a review session; refuse destructive
  reuse. Reopening an existing session uses status/serve, not init.
- `serve --session PATH --port 0`: start foreground HTTP server and print actual
  local URL. Support `--host` only for loopback by default. An opt-in exact
  `--allowed-origin URL` may support a trusted preview proxy; document constraints.
- `serve --workspace ROOT --port 0`: expose initialized local sessions directly
  inside ROOT as a graph catalog.
- `serve --repository-factory MODULE:CALLABLE --repository-config JSON_PATH
  --port 0`: serve an existing custom repository pair. Configuration is a JSON
  object; factory code is a trusted startup dependency. Select exactly one source:
  session, workspace, or repository factory. These commands serve shipped assets.
- `status --session PATH`: emit state JSON for agent review.
- `wait --session PATH --after-version N --timeout 20`: return state plus a changed
  boolean when a new version arrives or timeout elapses. Timeout is 0 through 30
  seconds. Refresh ephemeral presence; zero timeout supports a nonblocking pulse.
- `disconnect --session PATH`: clear ephemeral presence when pausing/ending a turn.
  Neither command changes semantic review state or creates approval.
- `validate --graph PATH`: validate graph structure/reference integrity.
- `propose --session PATH --graph PATH --summary TEXT --expect-version N`:
  validate and commit a new proposal, never a final graph.
- `annotate --session PATH --target-type TYPE [--target-id ID] --kind KIND
  --text TEXT --source chat|agent [--source-ref REF] --expect-version N`.
- `edit-annotation --session PATH --id ID [--text TEXT] [--kind KIND]
  --source chat|agent --expect-version N`: edit text and/or kind, retaining audit.
- `delete-annotation --session PATH --id ID --source chat|agent
  --expect-version N`: tombstone a note without erasing its review history.
- `resolve --session PATH --id ID --disposition TYPE --text TEXT --source
  chat|agent --expect-version N`; `reopen --session PATH --id ID --expect-version N`.
- `complete-review --session PATH --summary TEXT --expect-version N`: acknowledge
  a processed feedback request without creating a new round. Require no open
  annotations, clear review_requested, and record the summary in audit/history.
  This does not approve the graph; the user still approves the current version.
- `approve-chat --session PATH --text EXACT_USER_APPROVAL --source-ref REF
  --acknowledge-limits --expect-version N`: record actual approval expressed in
  chat for the current round. The agent must never invent this statement.
- `export --session PATH --out NEW_DIRECTORY --expect-version N`: generate final
  artifacts only if the exact current graph is approved and still settled. Refuse
  existing output paths; use an atomic new directory and never overwrite user work.

Commands print useful JSON or concise errors and return nonzero on failure.
All semantic state mutations except initialization require an expected version.

## Finalization

Write graph.json, index.json (compact node index with node paths), nodes/*.json,
dependencies.json, evidence.json, coverage.json, review.json (approval,
annotations, rounds, source snapshots), and manifest.json (schema, digest and
file hashes). Store the current approval and hash in review.json and the session
receipt. Finalization cannot silently omit open comments or known limitations.
The graph is compatible with requirement-refinement as input context; its report
validator is a different schema and must not be used as a graph validator.
