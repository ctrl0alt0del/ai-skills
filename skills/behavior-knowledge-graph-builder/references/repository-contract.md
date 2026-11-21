# Review app and repository contract

## Contents

- Packaged app
- Repository boundaries
- Graph identity and consistent reads
- Review writes and concurrency
- Bundled adapters and configuration
- Implementing another adapter
- Scope of the builder skill

## Packaged app

The skill ships a built web app in `assets/review-app/dist/`. Its manifest has
schema `behavior-review-app-dist/1`, entry `index.html`, and a `files` map from
served relative paths to SHA-256 hashes. The manifest does not include itself.
The packaged Python server verifies and serves the distribution. A missing or
corrupted distribution is a packaging error, never a reason to generate an app.

Maintain source and build tooling in the repository's `apps/behavior-graph-review/`
directory. A maintainer builds and commits the distribution before releasing the
skill. Node is a development tool; ordinary graph review uses the shipped files
and Python 3.10+ without a frontend package install or build.

## Repository boundaries

The app receives two repositories through dependency injection. Rendering and
review coordination do not know HTTP routes, database connections, filesystem
paths, buckets, or storage credentials. The bundled browser adapters implement
these ports over the same-origin HTTP API.

| Port | Operations |
| --- | --- |
| `GraphRepository` | `listGraphs()` and `getGraph(graphId, revision?)` |
| `HumanReviewRepository` | Read review state; list/get/create/update/delete annotations; resolve/reopen notes; request review; approve the current review |

The server has corresponding Python protocols in `scripts/graph_repositories.py`.
`GraphRepository` exposes `list_graphs()` and `get_graph(graph_id, revision=None)`.
`HumanReviewRepository` exposes `get_review`, `list_annotations`, `get_annotation`,
`create_annotation`, `update_annotation`, `delete_annotation`,
`resolve_annotation`, `reopen_annotation`, `request_review`, and `approve_review`.
Use the signatures in that module and the frontend declarations as the exact
implementation contract. Do not pass a SQLite connection or callback through a
port. Explicit review actions preserve workflow rules beyond annotation CRUD.

The default adapters may share local persistence, but neither port requires this.
A composition can read graphs from one service and save human reviews in another.
The graph and review schemas remain application contracts; storage independence
does not imply accepting arbitrary graph formats without an adapter.

## Graph identity and consistent reads

`listGraphs()` returns summaries with stable `id`, `title`, `revision`, and `digest`
fields. IDs are opaque to the app and independent of storage locations. Never use
a client-supplied graph ID directly as a filesystem path, bucket key, or SQL query.

`getGraph()` returns `{id, title, revision, digest, graph}`. A supplied revision
must identify an immutable snapshot for that graph. A missing revision requests
the latest available snapshot. In the local adapter, graph ID is `session_id`,
revision is the round number as a string, and revision `"0"` is the empty snapshot.

`getReview()` returns a review snapshot without graph content. It includes
`graph_id`, `graph_revision`, `graph_digest`, `state_version`, `round`, `rounds`,
annotations, approval, review request, finalization receipt, and agent presence.
Every `rounds` entry includes its own `graph_revision` and `digest`. Its numeric
`round` is a display/order value, not a storage revision. Use the explicit revision
reference for history reads; remote adapters may use object versions or opaque ETags.
Read the review first, then load exactly its referenced graph revision. Do not
combine the latest graph with an independently read older review.

Reject mismatched graph identity, revision, or digest. Keep the old view or report
an error when its referenced snapshot is temporarily unavailable. Graph selection
must scope drafts, layout, mutations, and asynchronous results; a slow response
from a previously selected graph must not replace the current view.

For cross-storage updates, publish immutable graph content before conditionally
advancing the review reference. A failed conditional update can leave an unused
snapshot; it must never approve or annotate an unintended revision. No distributed
transaction or automatic source watcher is supplied by this contract.

## Review writes and concurrency

Every semantic mutation carries the review's `expected_version`. The repository
must atomically check that version, apply the operation, increment `state_version`,
and record its audit event. A conflict returns HTTP 409 through the bundled API.
Preserve draft text and require a deliberate retry against the newly reviewed
state. Do not turn a conflict into a silent last-write-wins update.

- Create validates the target against the referenced graph snapshot.
- Update changes annotation text and/or kind. Target, original author/source, and
  creation metadata stay fixed; the previous value remains in audit. Editing a
  resolved note reopens it for review.
- Delete creates an audited tombstone. It removes the note from active review
  while retaining historical content. Deleted notes do not block approval and
  are not reopened by later graph proposals.
- Create, update, delete, resolution, reopening, and review requests invalidate
  previous approval and finalization. Proposal changes also invalidate approval.
- Approval checks the exact current graph/review state, all open notes, pending
  review requests, and explicit acknowledgment of limitations. It does not export
  a final graph or claim user authentication.
- Presence is operational metadata; it does not increment the semantic version
  or change approval.

There is no arbitrary review-state PUT endpoint. An adapter must not expose a
shortcut that lets callers insert approvals or skip these checks.

## Bundled adapters and configuration

Use one existing local session:

```bash
python3 scripts/graph_review.py serve --session /absolute/reviews/orders --port 0
```

Use a catalog of initialized sessions directly inside one workspace directory:

```bash
python3 scripts/graph_review.py serve --workspace /absolute/reviews --port 0
```

The local graph adapter reads immutable round snapshots. The local review adapter
uses the existing SQLite-backed `Store` for transactions, audit, review actions,
and shared browser/chat state. Existing session directories remain usable.

For an already implemented custom pair, configure a repository factory:

```bash
python3 scripts/graph_review.py serve \
  --repository-factory my_project.review_adapters:create_repositories \
  --repository-config /absolute/repository-config.json --port 0
```

The factory receives the configuration JSON object and returns
`Repositories(graphs=graph_repository, reviews=review_repository)`. Make its module
importable in the server environment. Treat factory code and configuration as
trusted startup inputs, never as values from graph content or annotations. Keep
storage credentials server-side and out of the built frontend and public errors.

## Implementing another adapter

Implement the two protocols independently and compose them in the factory. For
example, an S3 graph adapter can return immutable versioned objects while a database
review adapter stores versioned annotations and audit events. An API adapter can
forward the same operations to an existing service. These are extension examples;
this release does not include S3 drivers, cloud provisioning, or authentication.

Before using an adapter, verify graph/review ID isolation, immutable revisions,
missing-record errors, conditional concurrent writes, approval invalidation,
audit retention, and consistent source/digest references. A remote adapter must
use its backend's conditional-write or transactional facilities; local Python
process locks are insufficient for independent writers.

The supplied HTTP server keeps Host/Origin checks, JSON-only mutations and a
server-issued CSRF token. These protect the local browser workflow; they are not
an account or authorization system for public deployment.

## Scope of the builder skill

The skill investigates application behavior and authors/revises graph proposals.
It launches the released app and uses existing adapters. It does not generate the
review application, rebuild the distribution, or invent a storage integration
during normal graph review. The existing proposal and export CLI operates on
local sessions; a remote authoring/export workflow needs an explicit integration
that preserves the same approval and audit guarantees.
