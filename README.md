# AI Skills

Reusable skills for understanding application behavior and refining requirements.

## Skills

- [Behavior Graph Builder](skills/behavior-knowledge-graph-builder/SKILL.md):
  investigate application behavior, launch the shipped review app, collect graph
  annotations, resume outstanding feedback through a saved repository connection,
  and export approved graph artifacts as JSON.
- [Requirement Refinement](skills/requirement-refinement/SKILL.md): analyze a
  proposed requirement with two isolated investigators and a fresh evidence judge,
  producing concrete decisions, investigations, and regression checks in Markdown
  and JSON, with a separate review audit.

The behavior graph can be supplied as context to requirement refinement.
The skills use different JSON schemas and have separate validators.

The host's subagent tools run the investigators and judge. The refinement helper
freezes their shared input and validates candidate decisions and report lineage.
When isolated agents are unavailable, the skill records an explicit sequential
fallback; no external model API key or background service is required.

## Development and AI handoff

Read [HANDOFF.md](HANDOFF.md) before continuing development in a new thread or AI
session. It records the original goals, verified research references, design
decisions, implementation boundaries, graph/report contracts, validation status,
known limitations, and suggested next work.

## Installation

Copy either directory from `skills/` into your local Codex skills directory
(`$CODEX_HOME/skills`, normally `~/.codex/skills`). Keep its references, scripts,
and assets together.

The bundled Python tools use the standard library. Behavior Graph Builder and
the refinement review helper require Python 3.10 or newer.

Invoke the installed skills with `$behavior-knowledge-graph-builder` or
`$requirement-refinement`.

## Graph review app

The review app is developed in [apps/behavior-graph-review](apps/behavior-graph-review)
and its built distribution is committed inside the builder skill at
`assets/review-app/dist/`. The skill launches this released app. It does not generate
a frontend, install Node packages, or run a build during graph review.

The UI uses two injected repositories:

| Repository | Responsibility |
| --- | --- |
| `GraphRepository` | List available graphs and load a graph at an immutable revision |
| `HumanReviewRepository` | Read review state; create, read, edit, and delete annotations; resolve/reopen notes; request review and approve a settled version |

The browser's HTTP adapters and the server's local storage adapters are separate
from the UI. The default local catalog uses existing SQLite review sessions. A
repository factory can replace either server-side adapter with filesystem, S3,
database, or API integration code. Those additional production adapters are not
included. See the [repository contract](skills/behavior-knowledge-graph-builder/references/repository-contract.md).

Launch one session or a catalog of initialized direct child sessions:

```bash
python3 skills/behavior-knowledge-graph-builder/scripts/graph_review.py serve --session /absolute/review-session --port 0
python3 skills/behavior-knowledge-graph-builder/scripts/graph_review.py serve --workspace /absolute/reviews-root --port 0
```

### Feedback pickup across turns

The app and skill share a saved repository connection. A new session's `init`
command creates `connection.json`; serving an existing local session also creates
or reuses it. For a workspace or an existing custom adapter, use `connect` to save
the configuration. The skill reads that descriptor instead of assuming where
the app stores reviews.

```bash
python3 skills/behavior-knowledge-graph-builder/scripts/graph_review.py serve --connection /absolute/review-session/connection.json --port 0
python3 skills/behavior-knowledge-graph-builder/scripts/graph_review.py feedback-status --connection /absolute/review-session/connection.json
python3 skills/behavior-knowledge-graph-builder/scripts/graph_review.py pending-feedback --connection /absolute/review-session/connection.json
```

Use `feedback-graphs` to list the catalog and `--graph-id ID` when the descriptor
does not select a graph. Reads pin the review to its exact graph revision.
New or changed human feedback is **unprocessed** until the agent saves its outcome
and records a receipt for that exact feedback revision with `mark-processed`.
An edit, reopening, or deletion during the work remains pending; the agent cannot
acknowledge it using the earlier revision. Withdrawn notes appear in the pending
feed as tombstones, even though normal annotation lists hide them.

Processing is separate from discussion resolution and graph approval. An agent
can record `needs_clarification` while leaving a question open. Processing receipts
do not approve a graph or invalidate an existing approval. The review's “send
feedback” action remains the signal that the reviewer is ready for the agent;
pending feedback survives subsequent proposal changes and interrupted turns.

Feedback access uses an optional server-side `FeedbackProcessingRepository`
capability on the review adapter. The bundled local adapter implements it; an
older adapter can still display the app and explicitly report that processing
support is unavailable. Feedback commands can reconnect without a running viewer
server. They do not run a background model or add remote graph authoring/export.
Use one active feedback consumer and inspect saved outcomes before repeating work
after interruption. See the [repository contract](skills/behavior-knowledge-graph-builder/references/repository-contract.md)
for connection schemas, receipt fields, concurrency, and adapter requirements.

### Building a release

Build the app only when developing/releasing it (Node 18+, no package install):

```bash
node apps/behavior-graph-review/build.mjs
node apps/behavior-graph-review/build.mjs --check
```

The build fingerprints assets and records their hashes; it does not transpile or
minify JavaScript. The server validates the shipped manifest before serving files.

## Development checks

Run the Python suites, frontend tests, and distribution check from the repository root:

```bash
python3 -m unittest discover -s tests -p 'test_*.py'
node --test apps/behavior-graph-review/test/*.test.js
node apps/behavior-graph-review/build.mjs --check
```

These cover refinement artifact accounting and compatibility, graph/review adapter
contracts, scoped annotation CRUD, version conflicts, approval invalidation, built
asset serving, feedback pickup/receipts, and frontend coordination. They do not measure reasoning accuracy
or authenticate that a host actually created isolated agent contexts. The app's
README describes the optional browser smoke test and its environment requirements.
