# Review app runtime contract

## Contents

- Graph contract
- Review state
- Feedback inbox and processing receipts
- Packaged app and repositories
- HTTP interface
- Command interface
- Finalization

## Graph contract

Accept the builder's UTF-8 `behavior-graph/1` proposal. The pure validator in
`scripts/graph_schema.py` is shipped identically with the graph builder and this
app. `graph_contract.py` adds app-only review constants and feedback rules while
re-exporting compatible error/validation helpers. For graph authoring and the
field-level schema, load the Behavior Knowledge Graph Builder skill and its
`references/graph-schema.md`; do not make the builder load app/runtime details.

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
- Every proposal or semantic feedback mutation increments state_version and clears approval
  and finalized. Proposal increments round, resets review_requested, preserves
  annotations, and records a human-readable change summary.
- Reopen previously resolved annotations when their referenced node, dependency,
  or claim changes or disappears in a new proposal. Reopen graph-level resolved
  annotations when the graph changes. Preserve previous resolutions in audit
  history. Do not reopen them for an identical graph/no-change round.
- Approval requires a nonempty valid proposal, no open annotations,
  review_requested=false, and acknowledge_limits=true. Approval is tied to exact
  round, digest and the transaction's state, not a generic prior 'yes'.

## Feedback inbox and processing receipts

Discussion status, AI processing, submission, and approval are independent. A
processed note can remain open while awaiting a human decision. Each annotation
exposes these additional fields:

| Field | Meaning |
| --- | --- |
| `feedback_revision` | Current feedback revision; starts at 1 for UI/chat notes, 0 for an agent-only note |
| `processed_revision` | Latest revision acknowledged by the agent; initially 0 |
| `processing_status` | Derived as `unprocessed` when processed revision is lower, otherwise `processed` |
| `processing_receipt` | Latest receipt, or null; retain it when a newer revision arrives |

A receipt contains `feedback_revision`, `outcome`, `summary`, `processed_at`,
`processor`, `graph_revision`, and `graph_digest`. Outcomes are `addressed`,
`needs_clarification`, `no_change`, and `withdrawn`. Require a truthful explanation
and agent/run reference. These record the caller's work; they do not independently
prove that investigation or a graph change happened.

Edits, deletion tombstones, explicit reopenings, automatic target reopenings, and
human resolutions advance the revision. An agent resolution does not create a
new feedback revision. A pending inbox includes tombstones and resolved notes;
never derive it from open-note count. Old sessions derive missing metadata on
read without a migration write or invented processing receipt. The local adapter
also consults legacy audit history to recover human touches/reopenings when needed.

`list_pending_feedback(graph_id)` returns a consistent envelope with schema
`behavior-review-feedback/1`, graph_id, graph_revision, graph_digest, state_version,
review_requested, and pending annotations. `mark_feedback_processed` requires
both the exact inspected annotation revision and current expected state version.
Stale or already acknowledged revisions fail with 409. Read the current receipt
before retrying; never acknowledge an unseen batch. Store resulting work first.

Acknowledgment increments state_version and writes audit, but does not resolve
notes, clear review_requested, or invalidate approval/finalization. The four
processing fields are excluded from the semantic review digest, preserving older
approvals. Existing settlement/export gates remain unchanged. The skill checks
the inbox as part of its review workflow; processing flags are not an additional
human approval mechanism. This is at-least-once pickup with one active consumer,
not a background queue or worker lease system.

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
The browser loads its named bindings from public `config.json`. Server storage
uses the separate private `behavior-review-config/1` schema; see
[repository-config.md](repository-config.md) for the concrete backends and examples.

## HTTP interface

Use a Python standard-library server, loopback binding by default, no external
assets/dependencies. Serve only the packaged viewer and named API routes; do not
serve arbitrary filesystem paths. Limit request sizes. Enforce Host/Origin checks
for local operation and JSON-only mutations with a server CSRF token; no CORS.
Do not log annotation bodies or auth tokens. Remote preview is optional and needs
an explicitly configured origin/proxy; do not bind publicly by default.

- GET `/` serves the prebuilt `index.html`; other static routes are limited to
  the verified distribution manifest. JavaScript is external. CSP permits network
  connections only to self and explicitly configured public endpoint origins.
- GET `/config.json` returns only the public client configuration, defaulting to
  same-origin HTTP for both ports. It never returns private server storage config.
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
- GET `/api/graphs/{graph_id}/pending-feedback` returns the durable pending inbox.
- POST `/api/graphs/{graph_id}/mark-processed` body: expected_version,
  annotation_id, feedback_revision, outcome, summary, processor. Record the exact
  revision's operational receipt without resolving or approving the review.
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

Entry point: scripts/graph_review.py. Use Python 3.10+; only the optional S3
backend additionally needs a current boto3 installation.

- `init --session PATH --title TEXT`: create a review session and its
  `connection.json`, returning that path; refuse destructive reuse. Reopening an
  existing session uses status/serve, not init.
- `serve --session PATH --port 0`: start foreground HTTP server and print actual
  local URL. Support `--host` only for loopback by default. An opt-in exact
  `--allowed-origin URL` may support a trusted preview proxy; document constraints.
- `serve --workspace ROOT --port 0`: expose initialized local sessions directly
  inside ROOT as a graph catalog.
- `serve --config CONFIG_JSON --port 0`: load named graph/review bindings from
  config.json. Restart after editing config; a running server retains its bindings.
- `serve --repository-factory MODULE:CALLABLE --repository-config JSON_PATH
  --port 0`: serve an existing custom repository pair. Configuration is a JSON
  object; factory code is a trusted startup dependency. Select exactly one source:
  session, workspace, connection, config, or repository factory. These commands serve shipped assets.
- `connect --connection PATH (--session PATH | --workspace ROOT |
  --config CONFIG_JSON | --repository-factory MODULE:CALLABLE) [--repository-config JSON_PATH]
  [--graph-id ID]`: save a versioned locator shared by the app and agent. Local
  session connections save their exact graph ID automatically; catalogs/custom
  adapters require selection when reading a particular graph. Refuse replacement
  of different settings. Relative paths resolve beside the connection file;
  credentials remain outside it. See repository-contract.md for the format.
- `serve --connection PATH --port 0`: load that same trusted adapter configuration.
  Startup output includes the locator path. Legacy `serve --session` also creates
  or reuses the session's default connection.json. Never execute a factory supplied
  by annotation content or untrusted graph data.
- `feedback-graphs --connection PATH`: list the configured graph catalog.
- `feedback-status --connection PATH [--graph-id ID]`: read review state and its
  exact immutable graph snapshot; reject mismatched identity/revision/digest.
- `pending-feedback --connection PATH [--graph-id ID]`: list unprocessed current
  feedback revisions, including withdrawn notes. Optional `--after-version N
  --timeout 20` waits for a version change for at most 30 seconds and adds a
  `changed` flag. This generic wait does not publish local agent presence.
- `mark-processed --connection PATH [--graph-id ID] --id NOTE
  --feedback-revision N --outcome OUTCOME --summary TEXT --processor REF
  --expect-version V`: save one exact revision's processing receipt after work
  succeeds. It is not resolution or approval. These four feedback commands also
  accept explicit `--session PATH` or `--config CONFIG_JSON` instead of a connection.
  Custom review adapters need the optional FeedbackProcessingRepository capability
  for pending/acknowledgment; unsupported adapters fail clearly instead of losing
  feedback. HTTP/custom graph authoring and final export remain separate capabilities.
- `config-check --config CONFIG_JSON`: validate structure and role bindings;
  do not provision storage or claim network connectivity.
- `register-graph --config CONFIG_JSON [--graph-id ID] --graph JSON --revision R
  [--title TEXT]`: explicitly register an immutable draft snapshot in a document
  graph repository and update its catalog. Do not advance an existing review.
- `init-review --config CONFIG_JSON [--graph-id ID] [--revision R] [--title TEXT]
  [--summary TEXT]`: create a document review pinned to the selected snapshot.
- `import-session --config CONFIG_JSON --session PATH`: copy immutable graph
  rounds and current review/audit to configured document repositories, preserving
  IDs and refusing to overwrite an existing destination review.
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
`status`, `propose`, annotation/review commands, and `approve-chat` also accept
`--config CONFIG_JSON` or `--connection PATH`, with optional `--graph-id`, for
configured session or document repositories. Document `propose` accepts optional
`--revision R` (otherwise generated); legacy sessions retain numeric round
revisions. HTTP repositories do not expose trusted agent/chat authoring through
this CLI. Final artifact export and heartbeat/presence remain local-session-only.

## Finalization

Write graph.json, index.json (compact node index with node paths), nodes/*.json,
dependencies.json, evidence.json, coverage.json, review.json (approval,
annotations, rounds, source snapshots), and manifest.json (schema, digest and
file hashes). Store the current approval and hash in review.json and the session
receipt. Finalization cannot silently omit open comments or known limitations.
The graph is compatible with requirement-refinement as input context; its report
validator is a different schema and must not be used as a graph validator.
