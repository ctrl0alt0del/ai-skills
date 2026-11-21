# Behavior Graph Review app

This is the maintained source for the graph-review frontend bundled with
`behavior-knowledge-graph-builder`. The skill launches its packaged server and
serves `assets/review-app/dist`. Ordinary skill use needs Python 3.10+ and does
not scaffold an app, install frontend dependencies, or run a build.

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
filesystem, database, or credential access. The bundled
[src/http-repositories.js](src/http-repositories.js) implements the interfaces
using the local server's HTTP API. Only [src/main.js](src/main.js) chooses the
concrete adapters.

A deployment can retain the HTTP adapters and replace either server repository
through the server's repository factory. Filesystem, S3, database, and remote API
credentials belong in those server adapters. Alternatively, a different browser
transport can implement these interfaces and be supplied at the composition
root; the rendering and controller do not change.

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
Review changes invalidate prior approval; only explicit workflow operations may
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
`skills/behavior-knowledge-graph-builder/assets/review-app/dist`.
To build or check a different installed distribution:

```bash
node apps/behavior-graph-review/build.mjs --out /absolute/skill/assets/review-app/dist
node apps/behavior-graph-review/build.mjs --check --out /absolute/skill/assets/review-app/dist
```

The deterministic build fingerprints native ES modules and CSS, rewrites their
static references, and emits `index.html` plus `manifest.json`. It does not
transpile or minify JavaScript. The manifest records SHA-256 hashes for every
served artifact and a source/build digest; it includes no timestamps or machine
paths. `--check` verifies both the asset set and bytes without rewriting them.
Build refuses to overwrite an unrelated directory. The server serves only files
named in this manifest; the skill never repairs missing assets by generating UI.

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

The November 2026 implementation session passed the Node tests, but the rendered
browser check could not run because the environment prohibited Chromium's
required socket operation. This is not a successful browser/visual validation;
run the smoke check in a supported development environment before claiming that
coverage.
