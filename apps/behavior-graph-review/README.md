# Behavior Graph Review app

This is the maintained source for the graph-review frontend bundled with
`behavior-graph-review-app`. The skill launches its packaged server and
serves `assets/review-app/dist`. Ordinary skill use needs Python 3.10+ and does
not scaffold an app, install frontend dependencies, or run a build.

## Skill ownership and session orchestration

[Behavior Graph Review App](../../skills/behavior-graph-review-app/SKILL.md) owns
this application, its packaged runtime, repository configuration, server startup,
review mutations, approval/export operations, and continuation across prompts.
[Behavior Graph Builder](../../skills/behavior-knowledge-graph-builder/SKILL.md)
owns graph creation and updates from selected human feedback or source changes.
It does not operate the app or write review state.

The review-app skill creates or resumes a durable session, captures its exact
pinned graph and selected feedback revisions, and invokes the builder with a
versioned request. The builder returns a graph proposal, a change report,
remaining questions, and feedback recommendations. The app validates the result
against the current repository state before publishing it through `propose`.
An unchanged graph does not require another proposal round.

The app's `scripts/review_session.py` handles request preparation, result
application, and continuation status. Its checkpoint keeps repository locators
separate from builder input, records publication progress, and exposes work that
needs reconciliation after interruption. The graph/review repositories remain
authoritative. Stale state, mismatched identities, or ambiguous publication must
not become an automatic retry with a new version token.

Applying a builder result does not automatically resolve annotations, acknowledge
feedback, approve a graph, or export final artifacts. The coordinating skill
performs the justified follow-up through the existing review commands. It
re-reads annotations after publication because a changed graph target can reopen
a discussion and advance its feedback revision. A builder recommendation for an
older revision cannot consume that newer feedback.

See the [builder handoff reference](../../skills/behavior-graph-review-app/references/builder-handoff.md)
and [pure build contract](../../skills/behavior-knowledge-graph-builder/references/build-contract.md).
The helper does not run an LLM or a background loop; the host agent invokes the
builder and resumes the review on later prompts. App maintenance is performed
when requested against this maintained source project, then the reviewed
production build is shipped with the review-app skill. Ordinary graph review
uses the existing distribution.

## Architecture

The application has two replaceable repositories:

- **GraphRepository:** list available graphs and read a specific immutable graph
  revision.
- **HumanReviewRepository:** read a review and create, read, update, and delete
  its annotations, with explicit methods for resolving/reopening notes,
  requesting an agent review, and approving the exact settled version.

The contracts are in [src/repositories.d.ts](src/repositories.d.ts). Rendering in
[src/app.js](src/app.js) receives both repositories by injection. The
[src/controller.js](src/controller.js) coordinates them without DOM, HTTP,
filesystem, database, or credential access. [src/main.js](src/main.js) loads
`./config.json`, and [src/repository-config.js](src/repository-config.js) creates
the named repositories and independently binds graph reads and review writes.
Invalid configuration stops startup with a visible error; it does not silently
select a different store.

The browser and server have separate concrete adapters:

| Layer | Adapter | Capability |
| --- | --- | --- |
| Browser | [HTTP](src/http-repositories.js) | Graph reads, review CRUD/workflow, and optional agent feedback methods |
| Browser | [JSON documents](src/json-graph-repository.js) | Read a graph catalog and immutable snapshots from static HTTP(S) storage |
| Browser | [Memory snapshots](src/memory-graph-repository.js) | Read an explicit catalog and detached immutable snapshots supplied in configuration |
| Server | Filesystem | Store catalog, graph snapshots, review state, and audit as JSON documents |
| Server | SQLite | Store the same documents with transactional conditional writes |
| Server | S3 | Store the same documents with object-version conditional writes |
| Server | HTTP API | Forward graph, human-review, and feedback operations to another compatible service |
| Server | Session | Reuse an existing local SQLite review session or workspace of sessions |

Filesystem paths, database access, and S3/API credentials stay on the server.
The browser can use the HTTP adapter while the server independently selects any
supported graph and review storage. A custom adapter can still implement the
same interfaces without changing rendering or controller code.

## Configuration

### Server storage

The server configuration uses `behavior-review-config/1`. Each binding names an
entry in `repositories`, so graph content and human review can live in different
stores:

```json
{
  "schema_version": "behavior-review-config/1",
  "repositories": {
    "graphFiles": { "type": "filesystem", "path": "./data/graphs" },
    "reviewDatabase": { "type": "sqlite", "path": "./data/reviews.sqlite3" }
  },
  "bindings": { "graphs": "graphFiles", "reviews": "reviewDatabase" },
  "graph_id": "checkout"
}
```

Paths resolve relative to this configuration file. `s3` accepts `bucket` and
optional `prefix`, `region`, `profile`, and `endpoint_url`; it uses the SDK's
credential providers. `http` accepts `base_url` and optional `auth_token_env`,
which names an environment variable rather than storing a token. `session`
selects either `session` or `workspace`. Built-in S3 support requires the optional
`boto3` dependency and an endpoint that supports conditional object writes.

From the repository root, register a valid proposal and initialize its review:

```bash
python3 skills/behavior-graph-review-app/scripts/graph_review.py register-graph --config ./config.json --graph ./proposal.json --graph-id checkout --revision proposal-1
python3 skills/behavior-graph-review-app/scripts/graph_review.py init-review --config ./config.json --graph-id checkout
python3 skills/behavior-graph-review-app/scripts/graph_review.py serve --config ./config.json --port 0
```

`import-session --config PATH --session PATH` copies an existing local review
session into configured document storage. A saved connection can refer to the
same configuration with `connect --connection PATH --config PATH`.

### Public browser configuration

The frontend configuration uses the distinct schema
`behavior-review-client-config/1`. The server exposes only this public object at
`/config.json`; server storage locations and credentials are not included. By
default, it supplies one same-origin HTTP repository bound to both roles.

To choose browser transports, put a public object like this in the optional
`client` property of the server configuration. A standalone static deployment can
serve the same object as its own `config.json`:

```json
{
  "schema_version": "behavior-review-client-config/1",
  "repositories": {
    "publishedGraphs": { "type": "json", "url": "https://graphs.example/project/catalog.json" },
    "reviewService": { "type": "http", "base_url": "" }
  },
  "bindings": { "graphs": "publishedGraphs", "reviews": "reviewService" }
}
```

HTTP implements both roles. JSON and memory implement graph reads only; binding
either to `reviews` is an explicit configuration error. The memory form is
`{ "type": "memory", "catalog": { ... }, "snapshots": [ ... ] }`, with the
catalog declaring each graph's current revision and snapshots containing every
declared current revision plus any history. Snapshots are copied on input and
output so a caller cannot mutate the repository's stored version.

A JSON catalog is `{ "schema_version": "behavior-graph-catalog/1", "graphs":
[ ... ] }`. Each entry has `id`, `title`, `revision`, and `digest`. Immutable
snapshots contain those fields plus `graph`, and use this path relative to the
catalog's directory:

```text
graphs/<sha256-of-UTF8-graph-id>/<sha256-of-UTF8-revision>.json
```

This matches the server document stores. A pinned historical request reads its
exact snapshot without consulting the latest catalog. Latest-version requests
refresh the catalog; successful immutable reads are cached and returned as
detached copies.

Relative URLs resolve against the browser configuration's URL, preserving its
directory prefix. HTTP `base_url` is a service prefix; the adapter appends
`/api/...`. URLs require HTTPS except for HTTP on a loopback host. Embedded
credentials, query strings, fragments, and invalid URL characters are rejected.
Cross-origin deployments must permit the app origin through CORS, including any
required review token headers. Use the server HTTP adapter for private upstream
credentials.

Keep deployment configuration outside the installed app assets. The shipped
`config.json` is a default covered by the distribution manifest; the server's
public projection replaces its response without editing that authenticated file.
See [repository configuration](../../skills/behavior-graph-review-app/references/repository-config.md)
for complete schemas, adapter options, lifecycle commands, and migration details.

## Repository guarantees

Graph IDs are stable identities, independent of their storage paths. Revisions
are opaque strings. `getGraph(id, revision)` must return that exact immutable
revision; it must never silently return the latest version. Each review contains
`graph_id`, `graph_revision`, and `graph_digest`. The app reads the review and then
requests that pinned graph snapshot, checking identity, revision, and digest
before displaying the pair. Each historical round also supplies its own
`graph_revision`; the numeric round is only a display/order value.

Every mutation receives `expectedVersion`. Repositories must reject stale writes
with a conflict (the HTTP adapter exposes `status: 409`) and enforce review
invariants atomically. The app preserves unsaved text, loads the new state, and
requires an intentional retry. It does not implement blind write retries.
Semantic review changes invalidate prior approval; only explicit workflow operations may
approve a settled version. A repository must not implement these methods as an
unrestricted replacement of review state.

Annotation deletion is logical: it removes a note from active review while
retaining its previous value and deletion in audit history. Deleted notes have
`status: 'deleted'`, cannot be edited or reopened, and do not block settlement.
Editing a resolved note reopens it. The UI includes a separate deleted-notes
view; ordinary notes views omit tombstones.

The controller ignores obsolete reads and writes after graph switches, rejects
older state versions, and prevents late historical reads from replacing a newer
selection. Browser draft/layout storage is private presentation state scoped by
graph identity; the repositories remain authoritative for graphs and reviews.

## Agent feedback and shared connections

The review adapter also can implement the optional
`FeedbackProcessingRepository` capability. The bundled storage adapters and HTTP
client expose pending feedback and version-bound acknowledgment for the agent;
the human review UI does not acknowledge feedback automatically. This is not a
third storage repository or a model running in the browser. Custom adapters
without that capability remain usable for the existing review UI and must report
unsupported processing commands clearly.

Annotation snapshots add `feedback_revision`, `processed_revision`, derived
`processing_status`, and a `processing_receipt`. The receipt records the handled
revision, outcome, explanation, processor, time, and referenced graph. A newer
feedback revision retains the earlier receipt as history while becoming
unprocessed again. The UI can display these values independently of the note's
`open`, `resolved`, or `deleted` status.

New human notes and subsequent edits, deletions, reopenings, or human dispositions
need processing. Agent-only initial notes do not create human work, and an agent's
resolution does not enqueue its own response. Pending reads include tombstones
so a withdrawal cannot disappear while the agent handles an older copy.

The agent saves its work, then acknowledges the exact feedback revision with the
current expected review version. Stale acknowledgments fail. Outcomes are
`addressed`, `needs_clarification`, `no_change`, and `withdrawn`; they do not resolve
notes, clear submission requests, or approve the graph. Receipt bookkeeping
advances the review version but stays outside the semantic approval digest.

Use one saved `behavior-review-connection/1` descriptor for both `serve
--connection PATH` and the agent's `feedback-status`, `pending-feedback`, and
`mark-processed` commands. `init` writes a local session's `connection.json`;
`connect` records an existing session, workspace catalog, configured repository
pair, or trusted adapter factory. The same commands also accept `--config PATH`
directly. Relative paths resolve against the descriptor/configuration. A graph must be selected
explicitly by the descriptor or `--graph-id`, with `feedback-graphs` available for
discovery. Storage credentials stay in server/adapter configuration.

The “send feedback” action remains a readiness signal; saving a note alone need
not mean the reviewer finished. Processing state persists independently of that
signal. The active or resumed review-app skill reads the repository, with bounded polling
available; there is no background AI, permanent server guarantee, or worker lease.
Commands can load the adapter without a running viewer. The document adapters
support proposal registration and review initialization through `register-graph`
and `init-review`, with `import-session` for existing work. Remote HTTP authoring
requires a separate integration. Final graph artifact export remains the local
session workflow.

The complete schemas, CLI examples, and migration rules are in the
[repository contract](../../skills/behavior-graph-review-app/references/repository-contract.md).

## Ownership migration

Existing review data and its schemas do not move when the skill ownership changes.
Sessions, database files, config.json files, saved connections, graph IDs, and
processing receipts remain compatible. Update scripts or shortcuts that used the
old builder-owned `graph_review.py` path to
`skills/behavior-graph-review-app/scripts/graph_review.py`.

The builder keeps pure graph validation and build-result tooling. The app bundles
matching copies of the pure schema and handoff contracts, with parity checks, so
neither runtime imports a generated personal-skill directory name. Source changes
to those shared contracts must be released consistently in both packages.

## Build and checks

Node.js 18+ is required only for development/build. The build and unit tests use
Node's standard library; no package installation is needed.

From the repository root:

```bash
node apps/behavior-graph-review/build.mjs
node apps/behavior-graph-review/build.mjs --check
node --test apps/behavior-graph-review/test/*.test.js
```

The default output is
`skills/behavior-graph-review-app/assets/review-app/dist`.
To build or check a different installed distribution:

```bash
node apps/behavior-graph-review/build.mjs --out /absolute/skill/assets/review-app/dist
node apps/behavior-graph-review/build.mjs --check --out /absolute/skill/assets/review-app/dist
```

The deterministic build fingerprints native ES modules and CSS, rewrites their
static references, and emits `index.html`, a stable `config.json`, and
`manifest.json`. It does not
transpile or minify JavaScript. The manifest records SHA-256 hashes for every
served artifact and a source/build digest; it includes no timestamps or machine
paths. `--check` verifies both the asset set and bytes without rewriting them.
Build refuses to overwrite an unrelated directory. The server serves only assets
named in this manifest plus its explicit API/public-configuration routes; the
skill never repairs missing assets by generating UI.

Tests exercise independent bindings, malformed configuration, visible startup
failure, immutable graph reads, document identity checks, and conditional review
writes. S3 tests use controlled clients; no live cloud-storage integration test
has been performed for this change.

## Optional rendered-app check

[test/browser-smoke.mjs](test/browser-smoke.mjs) runs the built viewer against two
in-memory repositories, with no data API calls. It exercises graph selection,
annotation create/edit/delete, stale-edit recovery, literal text handling,
separate drafts per graph, historical read-only mode, and a mobile viewport.

With Playwright available to Node and its Chromium installed:

```bash
node apps/behavior-graph-review/test/browser-smoke.mjs
```

`PLAYWRIGHT_MODULE` can point to an installed Playwright module, and
`CHROMIUM_EXECUTABLE` can select an existing compatible Chromium executable.
These are test-only dependencies; they are not needed by the build or skill.

The automated unit and adapter checks are not rendered browser or visual
validation. Run the smoke check in a supported development environment before
claiming that coverage.
