# AI Skills

Reusable skills for understanding application behavior, reviewing behavior graphs,
and refining requirements.

## Skills and ownership

| Skill | Responsibility | Result |
| --- | --- | --- |
| [Behavior Graph Builder](skills/behavior-knowledge-graph-builder/SKILL.md) | Create a graph or update a supplied graph from human feedback, code changes, or both | Validated graph proposal, change report, unresolved questions, and feedback recommendations |
| [Behavior Graph Review App](skills/behavior-graph-review-app/SKILL.md) | Configure and start the shipped app, maintain a review across prompts, invoke the builder, and persist its results | A durable review session, exact feedback receipts, and human-approved graph artifacts when export is supported |
| [Requirement Refinement](skills/requirement-refinement/SKILL.md) | Analyze a proposed requirement with two investigators and an evidence judge | Concrete decisions, investigations, and regression checks in Markdown and JSON, with a separate review audit |

The builder owns graph reasoning and validation. The review-app skill owns app
setup, repositories, viewer startup, review mutations, approval, export, and session
continuation. It passes graph/evidence data to the builder and publishes the
returned proposal through the configured repositories. The builder can also run
on its own without a review server or storage adapter.

A behavior graph can be supplied as context to requirement refinement. The graph
and refinement report use different schemas and validators. The host launches
the refinement investigators and judge; when isolated agents are unavailable, the
skill records a sequential fallback. No background model service is required.

## Installation and invocation

Copy the desired directories from `skills/` into your local Codex skills directory
(`$CODEX_HOME/skills`, normally `~/.codex/skills`). Keep each skill's references,
scripts, and assets together. Install both graph skills for the complete review
workflow; the focused builder and requirement refinement can be used independently.

Invoke them with `$behavior-knowledge-graph-builder`,
`$behavior-graph-review-app`, or `$requirement-refinement`.

Python 3.10+ runs the supporting tools. The graph builder and local/HTTP review
storage use the Python standard library. The review app's optional S3 adapter
additionally requires `boto3`.

## Create and update graphs

Use the builder for three common requests:

- Create an initial graph from application code, specifications, contracts, and tests.
- Revise an existing graph from selected human review findings.
- Update an existing graph after source changes, preserving stable identities and
  the evidence versions of material that was not re-inspected.

The builder distinguishes intended policy, observed implementation, and proposed
changes. It returns unresolved questions instead of inventing decisions. Its
output is a proposal; it does not mutate a review, acknowledge feedback, record
human approval, or start an application.

See the [authoring guide](skills/behavior-knowledge-graph-builder/references/graph-authoring.md),
[graph schema](skills/behavior-knowledge-graph-builder/references/graph-schema.md),
and [builder handoff contract](skills/behavior-knowledge-graph-builder/references/build-contract.md).
A standalone graph can be checked with:

```bash
python3 skills/behavior-knowledge-graph-builder/scripts/graph_build.py validate --graph ./proposal.json
```

## Run a review across prompts

Use the review-app skill to create or resume the same review session, configure
its storage, and start the packaged viewer. It prepares a bounded builder request
with the exact current graph and feedback revisions, invokes the builder skill,
checks the returned result against the authoritative review, and publishes any
changed proposal. It then applies justified discussion outcomes and records exact
processing receipts through the existing review commands.

The app-owned continuation checkpoint retains the connection, graph selection,
request/result artifacts, publication phase, and continuation context. On a later prompt,
the skill re-reads the repositories and checkpoint before continuing. A stale
result or ambiguous interrupted publication requires reconciliation; resuming a
prompt must not blindly create a duplicate graph round. The helper does not
invoke a model or run an autonomous background loop.

The viewer and chat share one review. Processing feedback, resolving a discussion,
and approving a graph remain separate actions. A processed question can stay
open. The builder's recommendations do not authorize human approval.

See [review orchestration and builder handoff](skills/behavior-graph-review-app/references/builder-handoff.md)
and the [review runtime contract](skills/behavior-graph-review-app/references/runtime-contract.md).

## Review application and storage

The frontend source stays in [apps/behavior-graph-review](apps/behavior-graph-review).
Its built distribution now ships inside **`skills/behavior-graph-review-app`** at
`assets/review-app/dist/`. Ordinary review sessions launch those assets without
creating a frontend, installing Node packages, or building the app. Explicit app
maintenance belongs to the review-app skill and the maintained source project.

The app receives two repositories: `GraphRepository` lists graphs and reads exact
immutable revisions; `HumanReviewRepository` stores annotations, review workflow,
and feedback-processing receipts. A server `config.json` selects them independently:

```json
{
  "schema_version": "behavior-review-config/1",
  "repositories": {
    "graphs": { "type": "filesystem", "path": "./data/graphs" },
    "reviews": { "type": "sqlite", "path": "./data/reviews.sqlite3" }
  },
  "bindings": { "graphs": "graphs", "reviews": "reviews" },
  "graph_id": "checkout"
}
```

The server includes filesystem, SQLite, S3, compatible HTTP API, and existing
session/workspace adapters. The browser has separate HTTP, static JSON graph,
and memory graph adapters; durable review writes use HTTP. Credentials remain
server-side. Existing legacy session graph/review bindings must stay together;
importing into document storage enables independent bindings.

Launch an existing local session or configured review:

```bash
python3 skills/behavior-graph-review-app/scripts/graph_review.py serve --session /absolute/review-session --port 0
python3 skills/behavior-graph-review-app/scripts/graph_review.py serve --config /absolute/config.json --port 0
```

New storage requires explicit registration/initialization or session import.
Changing configuration does not migrate data. Use the [configuration guide](skills/behavior-graph-review-app/references/repository-config.md),
[examples](skills/behavior-graph-review-app/assets/repository-configs), and
[repository contract](skills/behavior-graph-review-app/references/repository-contract.md)
for provisioning, connections, and feedback commands.

### Migration from the combined builder

Existing session directories, databases, graph IDs, configuration schemas, and
connection descriptors remain usable. Change command paths from the builder's
former `scripts/graph_review.py` to the review-app skill's entry point shown above.
No review data migration is needed solely because skill ownership moved. Keep
runtime data and deployment configuration outside either installed skill.

Document repositories support graph proposals and review authoring. HTTP agent
authoring needs a compatible integration, and final artifact-bundle export still
uses local sessions. Moving ownership does not expand those backend capabilities.

## Development and AI handoff

Read [HANDOFF.md](HANDOFF.md) before continuing in a new thread or AI session. It
records the research references, ownership decisions, contracts, validation
history, and remaining work. Preserve the focused builder boundary when adding
new app or storage capabilities.

Build the app only when maintaining or releasing it (Node 18+, no package install):

```bash
node apps/behavior-graph-review/build.mjs
node apps/behavior-graph-review/build.mjs --check
```

The default release output is
`skills/behavior-graph-review-app/assets/review-app/dist`. The deterministic build
fingerprints assets and includes a public `config.json` in its manifest.

Run checks from the repository root:

```bash
python3 -m unittest discover -s tests -p 'test_*.py'
node --test apps/behavior-graph-review/test/*.test.js
node apps/behavior-graph-review/build.mjs --check
```

The tests cover graph/review contracts, storage, feedback, handoffs, and frontend
coordination. The two graph skills carry matching pure graph/build contract
modules so each package can load without a hardcoded installed sibling path.
S3 tests use controlled clients; current verification does not establish a live
cloud deployment or rendered browser layout. See the [app README](apps/behavior-graph-review/README.md)
for the optional browser smoke test.
