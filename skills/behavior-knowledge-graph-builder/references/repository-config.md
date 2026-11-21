# Configure graph and review repositories

## Contents

- One server config, two bindings
- Available backends
- Start with existing or new data
- Continue the same review through the skill
- Browser configuration
- Document format and concurrency
- Operational limits

## One server config, two bindings

Use a trusted `config.json` with schema `behavior-review-config/1`. Give each
repository a name, then bind graph reads and human reviews independently:

```json
{
  "schema_version": "behavior-review-config/1",
  "repositories": {
    "graphFiles": {"type": "filesystem", "path": "./data/graphs"},
    "humanReviews": {"type": "sqlite", "path": "./data/reviews.sqlite3"}
  },
  "bindings": {"graphs": "graphFiles", "reviews": "humanReviews"},
  "graph_id": "application-behavior"
}
```

Resolve paths beside the config file, regardless of the caller's working
directory. Names begin with a letter, contain only letters, digits, underscores,
or hyphens, and have at most 64 characters; reserved JavaScript object names are
not allowed. Unknown fields, unknown types, and missing bindings fail visibly.
Do not silently fall back to another repository. Validate all definitions but
instantiate only the repositories selected by the two bindings.

Keep `graph_id` optional for a catalog; select a particular graph with `--graph-id`
or a saved connection. IDs and revisions are opaque strings, not storage paths.
Two bindings may refer to the same repository or different ones. For example,
changing only `bindings.graphs` from `graphFiles` to a named S3 repository moves
future graph reads to that configured source; changing configuration does **not**
copy data. Provision or import the destination explicitly before using it.

Use [the shipped examples](../assets/repository-configs/) as configuration
templates. Copy the selected example into the project and replace its paths,
identifiers, and service settings. Do not put runtime data inside the skill.

## Available backends

All server backends implement the existing graph/review contracts. Document
backends reuse one review state machine; they do not independently reimplement
approval, annotation, and processing rules.

| `type` | Configuration | Persistence and dependencies |
| --- | --- | --- |
| `filesystem` | `path`: document directory | JSON documents, atomic replacement, per-document OS interprocess locks; Python standard library |
| `sqlite` | `path`: database file | JSON documents in a transactional SQLite database with versioned compare-and-swap; Python standard library |
| `s3` | `bucket`; optional `prefix`, `region`, `profile`, `endpoint_url`, `max_bytes` | S3 objects with conditional writes; optional current `boto3` required |
| `http` | `base_url`; optional `auth_token_env`, `timeout`, `max_bytes` | Forward the graph/review API to an existing service; Python standard library |
| `session` | Exactly one of `session` or `workspace` | Compatibility with existing `review.sqlite3` sessions; workspace discovery is one directory deep |

The `sqlite` document database and the `session` adapter have different storage
schemas. Do not point `sqlite.path` at an existing session's `review.sqlite3`.
Use `session` to reuse it, or `import-session` to copy it to the document format.
When reviews use a legacy session, both bindings must name that same repository:
its authoring operations store graph and review together. Use document backends
after import to select graph/review locations independently.
SQLite is the supplied database driver; PostgreSQL and other databases need a
separate document-store adapter or the existing trusted factory extension.

Example S3 and API definitions, selectable by either binding:

```json
{
  "cloudGraphs": {
    "type": "s3",
    "bucket": "your-graph-bucket",
    "prefix": "behavior-review/project-a",
    "region": "eu-central-1"
  },
  "reviewApi": {
    "type": "http",
    "base_url": "https://review.example.com/project-a",
    "auth_token_env": "REVIEW_API_TOKEN",
    "timeout": 15
  }
}
```

Keep actual tokens out of JSON. HTTP reads the bearer token from the named
environment variable at request time; S3 uses the SDK credential chain, including
configured profiles and workload roles. Only selecting S3 requires `boto3`.
An existing bucket/prefix is required; no cloud resources or policies are created.
Install its optional dependency in the server/agent Python environment when
authorized, using `python3 -m pip install --upgrade boto3` or the host's approved
environment manager. Do not install frontend dependencies or rebuild the app.

HTTP `base_url` is the service root, optionally with a reverse-proxy prefix, before
the API's `/api/graphs` routes. It is not an arbitrary REST endpoint: the remote
service must implement the documented graph, review, bootstrap-token, and optional
feedback API. HTTPS is required except for loopback development addresses.
Redirects are rejected, response sizes are bounded, and write conflicts are not
automatically retried. The optional `timeout` is in seconds (positive, at most
120); HTTP and S3 `max_bytes` default to 16 MiB (configurable from 1 KiB to 128 MiB).
Mutation request bodies remain limited to 128 KiB.

## Start with existing or new data

Run commands from the skill directory, or substitute the absolute script path.
First validate configuration structure without provisioning or network checks:

```bash
python3 scripts/graph_review.py config-check --config /project/config.json
```

For an existing local session that already has at least one graph proposal:

```bash
python3 scripts/graph_review.py import-session --config /project/config.json --session /project/old-review
```

Import preserves the source graph/session ID, immutable round snapshots, review
version, annotations, processing receipts, approval, and audit. Use the returned
graph ID in the config or connection. The source remains unchanged. An existing
destination review is never replaced. Both selected repositories must support
document provisioning; graph import cannot publish through the read-only HTTP
graph port. A partially interrupted import may leave immutable graph documents;
retry only after checking the destination, and never discard conflicting reviews.

For a new graph/review in document storage, register the first **draft** snapshot
and explicitly create its review:

```bash
python3 scripts/graph_review.py register-graph --config /project/config.json --graph /project/proposal.json --graph-id application-behavior --revision draft-1
python3 scripts/graph_review.py init-review --config /project/config.json --graph-id application-behavior --revision draft-1 --summary "Initial proposal for review"
python3 scripts/graph_review.py serve --config /project/config.json --port 0
```

Registration validates the existing behavior graph schema and creates an immutable
revision. Re-registering that ID/revision with different content fails. It does
not advance an existing review, approve a graph, or produce final artifacts.
Reads and adapter constructors do not create directories, databases, buckets,
catalogs, or reviews. `register-graph` explicitly creates/updates the catalog;
an unprovisioned catalog returns a missing-record error until that step completes.

Keep one connection for resuming both consumers, if useful:

```bash
python3 scripts/graph_review.py connect --connection /project/review-connection.json --config /project/config.json --graph-id application-behavior
python3 scripts/graph_review.py serve --connection /project/review-connection.json --port 0
```

The descriptor contains `{"kind":"config","path":"config.json"}` under
`repository`. The config path resolves beside the descriptor; data paths resolve
beside the config. The app and CLI load the same bindings. Restart the server after
changing its config; a running server retains the repositories chosen at startup.

## Continue the same review through the skill

Use `--config` directly or `--connection` for the saved descriptor:

```bash
python3 scripts/graph_review.py feedback-graphs --config /project/config.json
python3 scripts/graph_review.py feedback-status --config /project/config.json --graph-id application-behavior
python3 scripts/graph_review.py pending-feedback --config /project/config.json --graph-id application-behavior
```

The first command lists available graphs. Status loads the review and its pinned
graph, not whichever revision is newest in the catalog. Feedback commands work
for filesystem, SQLite, S3, session, and a compatible HTTP service. Reads and
receipts do not depend on a running local viewer; HTTP naturally depends on its
configured remote service. Use `mark-processed` with the exact annotation revision
and latest review version only after saving the work described in the receipt.

Filesystem, SQLite, and S3 document reviews also expose the existing trusted
agent/chat authoring commands through `--config` or `--connection`: `status`,
`propose`, `annotate`, `edit-annotation`, `delete-annotation`, `resolve`, `reopen`,
`complete-review`, and `approve-chat`. Preserve real sources and user statements.
For a further round:

```bash
python3 scripts/graph_review.py propose --config /project/config.json --graph-id application-behavior --graph /project/next-proposal.json --summary "Clarified cancellation behavior" --revision draft-2 --expect-version 7
```

Read the actual version first. Omit `--revision` to generate an opaque unique
revision. Proposal changes preserve notes, reopen settled notes whose referenced
targets changed, and clear obsolete approval. Separate registration alone does
not change the review pin. The graph backend must support publication for
`propose`; an HTTP-only graph port does not provide it. HTTP review adapters expose
human UI operations and feedback processing, but require the remote service's own
integration for trusted agent/chat authoring. Do not mislabel an agent action as
human UI input to work around that boundary.

Final artifact `export` and ephemeral `wait`/`disconnect` presence currently remain
local-session capabilities. Do not imply that a document review can already export
the final artifact bundle. Report the missing export capability if that becomes
necessary; leave approval unchanged and retain the settled review. Document
feedback can use the bounded `pending-feedback --after-version N --timeout 20`
wait instead. No background model or worker lease is supplied.

## Browser configuration

The shipped `main.js` loads `./config.json` before constructing repositories. Its
public schema is `behavior-review-client-config/1`, deliberately separate from
the private server schema. With the packaged server, `GET /config.json` returns
only `serverConfig.client`, or this default when `client` is omitted:

```json
{
  "schema_version": "behavior-review-client-config/1",
  "repositories": {"local": {"type": "http", "base_url": ""}},
  "bindings": {"graphs": "local", "reviews": "local"}
}
```

The default browser HTTP calls use the server repositories selected above. Leave
this default in place for filesystem, SQLite, S3, or authenticated remote API
storage; their credentials and private paths stay server-side.

For an intentionally public static catalog, an optional server `client` object or
a separately hosted browser `config.json` can choose another graph transport:

```json
{
  "schema_version": "behavior-review-client-config/1",
  "repositories": {
    "publishedGraphs": {"type": "json", "url": "https://graphs.example.com/project-a/catalog.json"},
    "reviewApi": {"type": "http", "base_url": ""}
  },
  "bindings": {"graphs": "publishedGraphs", "reviews": "reviewApi"}
}
```

| Browser type | Graph reads | Durable review operations |
| --- | --- | --- |
| `http` with optional `base_url` | Yes | Yes |
| `json` with `url` | Yes, static catalog and immutable snapshot files | No |
| `memory` with `catalog` and `snapshots` | Yes, injected read-only snapshots for demos/integrations | No |

A `memory` repository takes a `behavior-graph-catalog/1` object and an array of
GraphSnapshot objects. It clones all returned data and never persists reviewer
input. Selecting JSON or memory for the review binding fails; no feedback silently
falls back to browser storage. Browser configuration has no credential fields.

Resolve relative URLs against the browser config URL, including its path prefix.
Only HTTP(S) endpoints without embedded credentials, queries, or fragments are
accepted; non-loopback endpoints require HTTPS. An invalid/missing config shows
a startup error and does not contact a default repository. For cross-origin
sources, configure the upstream service's CORS and authentication intentionally.
The packaged server permits connections only to itself and the exact public
origins named in `client`; it does not enable cross-origin review writes to its
own API. The server's graph repository must still supply the snapshots referenced
by its reviews even when the browser reads a separately published copy.

The built `dist/config.json` is included in the asset manifest. Do not edit a
manifest-authenticated release file when using the packaged server; provide the
server config's `client` object instead. Maintainers change `src/config.json` and
rebuild only when releasing a different standalone default.

## Document format and concurrency

Document adapters share these keys inside the configured directory, database,
or S3 prefix:

| Key | Value |
| --- | --- |
| `catalog.json` | `{"schema_version":"behavior-graph-catalog/1","graphs":[GraphSummary]}` |
| `graphs/<sha256(graph_id)>/<sha256(revision)>.json` | Immutable `{id,title,revision,digest,graph}` |
| `reviews/<sha256(graph_id)>.json` | `{"schema_version":"behavior-human-review-document/1","state":ReviewState,"audit":[...]}` |

Hash the exact UTF-8 ID/revision bytes; do not trim or normalize opaque identities.
Graph summaries include `node_count`. Review state contains its exact
`graph_revision`, `graph_digest`, and each historical round reference; it does not
embed graph content. The static JSON browser adapter uses the same catalog and
snapshot paths. The shared domain code temporarily loads the pinned graph to
check annotation targets and approval, then persists only review state and audit.

The storage port is `read(key) -> (document, token)`, `create(key, document)`, and
`compare_and_swap(key, document, expected_token)`. Creation is absent-only and CAS
rejects stale/deleted documents. Domain mutations also check the user-visible
`state_version`. Persist state and its audit together in one conditional document
write. Filesystem uses OS advisory locks plus atomic replacement; SQLite uses a
write transaction and revision token; S3 uses `IfNoneMatch="*"` for creation and
`IfMatch=ETag` for replacement. Never replace failed conditional writes with an
unconditional PUT. See [AWS conditional writes](https://docs.aws.amazon.com/AmazonS3/latest/userguide/conditional-writes.html)
and [Boto3 PutObject](https://docs.aws.amazon.com/boto3/latest/reference/services/s3/client/put_object.html).

Publish immutable graph content before advancing catalog or review references.
The catalog and a review in another repository cannot be updated in one database
transaction. An interrupted/conflicting proposal can therefore leave an unused
snapshot or a catalog newer than the review; the review pin remains authoritative.
Reload and reconcile after conflicts. Do not garbage-collect historical snapshots
that any review round still references.

## Operational limits

Each review and its audit is currently one bounded JSON document; this is suitable
for bounded review sessions, not an unbounded enterprise event log. Split storage
or archival needs require another adapter. Filesystem advisory-lock guarantees
assume a filesystem that correctly supports those locks and atomic rename/link;
they are not an endorsement of every network filesystem. S3-compatible services
must honor both conditional PutObject preconditions atomically. Current tests
exercise an S3 protocol fake, not a deployed AWS bucket or every compatible vendor.

The bundled HTTP server keeps its local Host/Origin/CSRF protections and is not a
public account/authentication server. Preserve deployment access controls. Do not
infer cloud provisioning, multiuser authentication, automatic source watching,
distributed transactions, or final export support from the existence of a
configurable repository.
