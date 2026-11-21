# Maintainer and AI handoff

**Purpose:** preserve the problem, design decisions, research lineage, implementation boundaries, and next work for another developer or AI session.

**Baseline:** reviewed on 2026-11-20 against [commit `20fe0288cc01521a445c289284f3f7af73de4265`](https://github.com/ctrl0alt0del/ai-skills/tree/20fe0288cc01521a445c289284f3f7af73de4265). Recheck the current code before relying on implementation details below. This document records the initial design; the skill instructions, contracts, and implementation must evolve together.

**Implementation update, 2026-11-20:** Requirement Refinement now prescribes two
isolated investigators followed by a fresh evidence judge when the host supports
subagents. Dedicated role guides and a deterministic review helper make the
handoffs explicit and validate their lineage and candidate accounting. The final
report remains schema `1.0`; execution provenance is saved separately. The host
launches the agents; no model API runner has been added.

**Implementation update, 2026-11-21:** Behavior Graph Builder now launches a
prebuilt review app maintained in `apps/behavior-graph-review/`; its distribution
ships with the skill. The UI and HTTP server depend on separate graph and human
review repository contracts. Annotation editing and audited deletion are
implemented. Normal skill use never creates or builds the app. The initial local
adapter boundary has since been expanded by the configuration release below.

**Feedback update, 2026-11-21:** The app and agent can share a saved repository
connection. The skill discovers outstanding feedback through an optional review
adapter capability and records a receipt for each handled feedback revision.
New edits, withdrawals, reopenings, and human dispositions remain discoverable
across turns. Processing is separate from note resolution and human approval;
this change adds no background model. Configured document authoring is now
supported as described below; HTTP authoring still needs its own integration.

**Repository/configuration update, 2026-11-21:** Server storage now includes
filesystem JSON documents, SQLite documents, S3, a compatible HTTP API, and the
existing session/workspace adapter. A strict `config.json` names repositories and
binds graph reads and human reviews independently. The browser loads a separate
public configuration and includes HTTP, static JSON graph, and memory graph
adapters. Review state, audit, and feedback receipts share one conditional
document write and one domain implementation. Document storage supports graph
registration, review initialization, proposal rounds, and explicit session
import; final artifact export remains a local-session operation. No live project
data was migrated during this release. S3 verification uses controlled SDK
clients, and no successful visual/browser validation is claimed.

## Contents

1. [Start here](#1-start-here)
2. [Problem and intended outcome](#2-problem-and-intended-outcome)
3. [Research and prior discussion](#3-research-and-prior-discussion)
4. [Design decisions and their reasons](#4-design-decisions-and-their-reasons)
5. [Current workflows](#5-current-workflows)
6. [Graph semantics and integration contract](#6-graph-semantics-and-integration-contract)
7. [Runtime and source map](#7-runtime-and-source-map)
8. [Verification status and known limits](#8-verification-status-and-known-limits)
9. [Suggested next work](#9-suggested-next-work)
10. [Prompt for the next session](#10-prompt-for-the-next-session)

## 1. Start here

Read this document, then:

1. [Behavior Graph Builder instructions](skills/behavior-knowledge-graph-builder/SKILL.md).
2. Its [graph authoring guide](skills/behavior-knowledge-graph-builder/references/graph-authoring.md), [runtime contract](skills/behavior-knowledge-graph-builder/references/runtime-contract.md), [repository contract](skills/behavior-knowledge-graph-builder/references/repository-contract.md), [repository configuration guide](skills/behavior-knowledge-graph-builder/references/repository-config.md), and [chat review guide](skills/behavior-knowledge-graph-builder/references/chat-review.md).
3. [Requirement Refinement instructions](skills/requirement-refinement/SKILL.md).
4. Its [impact relationships](skills/requirement-refinement/references/impact-relationships.md), [edge-case taxonomy](skills/requirement-refinement/references/edge-case-taxonomy.md), and [report contract](skills/requirement-refinement/references/output-schema.md).
5. The relevant implementation files in [section 7](#7-runtime-and-source-map).

The two skills have different responsibilities:

| Skill | Input | Result |
| --- | --- | --- |
| Behavior Graph Builder | Application code, specifications, contracts, tests, and human knowledge | A reusable, reviewed behavior graph with evidence, coverage limits, and approval history |
| Requirement Refinement | A proposed requirement plus available project evidence, optionally including a behavior graph | A prioritized agenda of decisions, investigations, and regression checks |

**Implementation boundary:** these are agent workflows with supporting tools. The AI investigates and reasons according to the Markdown instructions. Python implements structural validation, refinement review handoffs/audits, and, for the builder, review persistence, serving, and export. There is no bundled autonomous repository analyzer, embedding index, DBSCAN pipeline, or general impact-analysis engine.

## 2. Problem and intended outcome

The original problem was the amount of developer, designer, QA, and product discussion needed to decompose a feature and settle its edge cases. An AI should help prepare that discussion by locating relevant behavior and exposing consequential unanswered questions.

Loading more source code does not by itself supply missing business intent. Code may implement an old rule, a test may cover only one scenario, and two features may interact through shared state or an event without calling one another. The useful knowledge therefore includes behavior, conditions, invariants, dependencies, and the authority and freshness of supporting evidence.

The intended sequence is:

```mermaid
flowchart TD
    S["Code, specifications, contracts, tests"] --> P["Behavior proposal"]
    P --> R["Human review in viewer or chat"]
    R -->|"Corrections or missing behavior"| P
    R -->|"Approve settled version"| G["Approved graph snapshot"]
    G --> A["Requirement refinement"]
    N["New requirement"] --> A
    S --> A
    A --> O["Decisions, investigations, regression checks"]
```

The graph is reusable context that helps the refinement agent find a relevant neighborhood without loading the whole application. It is a versioned representation of what has been established and what remains uncertain. Approval does not prove completeness or establish every stated runtime guarantee.

Refinement must also work before a permanent graph exists. It supports repository-backed, documentation-only, and requirement-only analysis, constructing a temporary impact map when useful.

The user's explicit builder requirements were: start a server and an understandable review frontend on first use; allow annotations; revise in one or more rounds; continue through chat when useful; and create the final graph only after the current proposal is settled and approved. The subsequent app requirement is to develop and build the app independently, ship its distribution with the skill, and isolate graph reads and human-review CRUD behind replaceable repositories.

The feedback follow-up requires the skill to know where the app saves reviews,
pick up new work, and mark it processed after handling it. The implementation uses
a shared connection descriptor and per-feedback-revision receipts so storage can
change without losing that workflow or consuming a newer human edit accidentally.

The repository/configuration follow-up asks for actual storage implementations,
selected through a configuration file rather than a source-code edit. Graphs and
reviews must be independently placeable, while the viewer and a resumed agent
continue to use the same selected repositories and review semantics.

## 3. Research and prior discussion

The original discussion identified the sources below. Their canonical links were reconstructed and checked while writing this handoff; not every original citation URL survived the conversation history. Distinguish research inspiration, related tools, and actual implemented capabilities.

### Primary inspiration: DRILS

Romina Etezadi et al., **[DRILS: Dependency-based Requirement Change Impact Analysis Using LLMs](https://arxiv.org/abs/2610.33920v1)**, arXiv:2610.33920v1, submitted 24 October 2026. [Full text](https://arxiv.org/html/2610.33920v1); [DOI](https://doi.org/10.48550/arXiv.2610.33920).

DRILS accepts requirements, an existing requirement dependency graph, and a change request. It combines Requirement-Enrichment RAG (RE-RAG), DBSCAN grouping, two independent impact generators, and judge consolidation. Its relationship vocabulary includes Requires, Implements, Conflicts, and Details; Conflicts is undirected.

The paper reports average recall of 87.1%, precision of 76.3%, and approximately 81% F1 on three automotive requirement datasets. These are the authors' results, not measurements of these skills. Its replication package is described as forthcoming upon acceptance.

The repository adapts focused context gathering, typed relationships, complementary generation, and separate evidence review. It does not reproduce the paper's model configuration, preprocessing, clustering, confidence scoring, or evaluation. The human-reviewed behavior graph builder and concrete edge-case agenda are project extensions.

### Related research: semantic and structural impact

Momil Seedat, **[Toward Semantically-Seeded, Graph-Propagated Impact Analysis Across Software Artifacts: A Vision](https://arxiv.org/abs/2606.18855)**, 17 June 2026.

This explores combining semantic similarity with decayed propagation over typed artifact dependencies. The reported proof of concept uses five change scenarios in a small payment subsystem. It is relevant to future retrieval experiments, with limited evidence of generalization. No propagation matrix, decay coefficient, or blended ranking formula from this paper is implemented here.

### Potential evaluation resource: Safa

Katherine R. Dearstyne, Jane Cleland-Huang, and Alberto Rodriguez, **[Real-World Traceability Patterns for Generative AI Systems: With Insights from the Safa Dataset](https://doi.org/10.1145/3786174.3788354)**, RAISE '26.

The [official dataset](https://zenodo.org/records/18285767), published 18 January 2026, provides natural-language artifacts and source code associated with the paper. [Dataset DOI](https://doi.org/10.5281/zenodo.18285767). This is a candidate for future evaluation; neither skill has been evaluated against it in the recorded work. Do not confuse it with the older Dronology / SAFA-Artifacts safety-case dataset.

### Related approaches discussed

| Reference | Relevance to this project |
| --- | --- |
| [GitHub Spec Kit](https://github.com/github/spec-kit) | Structured specification and implementation workflows |
| [Codebase Memory MCP](https://github.com/DeusData/codebase-memory-mcp) | Persistent technical code knowledge; a possible future evidence source |
| [Swimm documentation](https://docs.swimm.io/) | Capturing and retrieving codebase knowledge |
| [Ketryx](https://www.ketryx.com/) | Engineering traceability and change-impact workflows |
| Matt Wynne, [Introducing Example Mapping](https://cucumber.io/blog/bdd/example-mapping-introduction/), 8 December 2015 | Organizing refinement around rules, concrete examples, and unresolved questions |

These are references and possible comparison points, not runtime dependencies or existing integrations. Cucumber was part of the earlier discussion; the specific Example Mapping article is useful supporting reading identified for this handoff. An unidentified systematic review was also mentioned earlier; it is deliberately not reconstructed as a specific citation without evidence.

## 4. Design decisions and their reasons

This table records choices expressed by the user or embodied in the current instructions and code. It does not imply that every possible alternative was experimentally compared.

| Decision | Reason and consequence |
| --- | --- |
| Separate graph construction from requirement refinement | A reviewed map can be reused across changes. A single change can still be analyzed without first documenting the whole application. |
| Use business behavior as the main unit | Function names and call edges alone do not capture eligibility, lifecycle, policy, or shared-data assumptions. Technical artifacts remain evidence for behavioral claims. |
| Separate intended, observed, and proposed behavior | A product statement does not prove the implementation; existing code does not authorize policy. Contradictions remain visible. |
| Attach evidence and uncertainty to individual claims | A node can contain both well-supported facts and unknown guarantees. One label for an entire feature would hide that distinction. |
| Preserve stable semantic IDs and source versions | Review notes and downstream references should survive rewording. Versions let a later analysis question freshness instead of trusting a familiar filename. |
| Use typed edges; explain change-specific impact during refinement | The baseline records persistent coupling and conditions. Refinement explains how this particular change could propagate; connectivity alone is insufficient. |
| Traverse incoming as well as outgoing relationships | Shared-state readers and event consumers are often discoverable through incoming edges. Edge direction must retain its meaning. |
| Retrieve focused evidence and maintain an unresolved frontier | Work can continue within context limits without silently losing important unexplored branches. |
| Avoid a fixed hop cutoff or a finding quota | Neither a cluster boundary nor a hop count proves that downstream consequences end there. Real limits must be reported as coverage limits. |
| Use two fresh investigators and a fresh judge | Business and failure analysis expose different omissions without sharing initial candidate answers. A third context checks evidence and consolidates their completed results; agreement between agents is insufficient evidence. |
| Separate decisions, investigations, and regressions | The team should decide undefined policy, investigate missing facts, and test established rules without being asked to approve the same rule again. |
| Preserve unknowns and accepted gaps | Human review can settle how a limitation is represented without inventing knowledge about an inaccessible service. |
| Keep browser and chat review in one session | A modality change should not lose feedback, create contradictory copies, or bypass approval. |
| Require approval of the exact settled proposal | Reviewing an older graph does not authorize a changed graph. One successful round is sufficient. |
| Develop the review app separately and ship its built distribution | Normal skill use launches known, reviewed assets without creating an app or invoking frontend build tooling. The manifest and build check detect missing, corrupted, or stale distribution files. |
| Inject separate graph and human-review repositories | The UI can read graphs and save review data without knowing storage paths or database drivers. Filesystem, SQLite, S3, HTTP, and legacy session implementations now ship; storage-specific credentials stay behind the API. |
| Select named repositories through configuration | Independent `graphs` and `reviews` bindings support combinations such as S3 graphs and SQLite reviews without editing the app. Unknown fields, unsupported types, missing names, and incompatible capabilities fail explicitly. |
| Separate server storage configuration from public browser configuration | Server schema `behavior-review-config/1` can contain paths and credential-provider references. The browser receives only `behavior-review-client-config/1`; its HTTP/JSON/memory choices never receive the private server definitions. |
| Reuse one review domain across storage backends | `ReviewActions` owns annotation, proposal, approval, and feedback rules for legacy sessions and document-backed reviews. New persistence drivers must not fork those rules or replace review state without applying them. |
| Commit review state, audit, and receipts in one compare-and-swap document | Filesystem, SQLite, and S3 have different transaction facilities. One `state` plus `audit` document prevents a successful state change from losing its audit or processing receipt, and rejects stale writes without partially saving them. Graph publication remains a separate immutable operation. |
| Hash opaque graph identities and revisions into document keys | A slash or Unicode character remains part of the identity, without becoming a filesystem traversal or object-prefix rule. Static JSON readers and server stores share the exact SHA-256/UTF-8 layout. |
| Keep legacy session bindings together | A `session` review binding must use the same named repository as the graph binding because legacy proposal authoring writes its session's own graph source. Use document-backed review storage for independent bindings. |
| Make provisioning and migration explicit | Ordinary reads do not create repositories. `register-graph`, `init-review`, and `import-session` perform deliberate writes; choosing a new configuration does not silently move an existing user's data. |
| Use explicit immutable revision references and conditional review writes | Separate storage systems cannot share an assumed transaction. Review and history reads name their exact graph revision; stale writes fail rather than overwriting concurrent feedback or approving different content. |
| Persist one repository connection for app and agent | A later turn can find the same review adapter without guessing a SQLite path or depending on a remembered server process. Graph selection stays explicit. |
| Track processed feedback separately from discussion status and approval | Reading and responding to a question may still leave a human decision open. Processing receipts do not resolve notes or change an otherwise valid approval. |
| Acknowledge exact feedback revisions and include withdrawals | Edits, reopening, and deletion during an agent's work must stay visible. A stale acknowledgment must not consume the newer input. |
| Use recoverable processing with one active consumer | Existing conditional writes and audit suffice for this workflow. A crash can leave saved work pending, so resumed agents inspect previous outcomes before repeating it; no lease or exactly-once claim is introduced. |
| Use optimistic version checks for semantic mutations | The agent and reviewer can act concurrently; stale edits must be surfaced and reconciled. |
| Keep agent presence outside semantic review state | A heartbeat should not invalidate human approval. A running server must not imply that an AI is continuously running. |
| Export a complete graph plus index and node records | Consumers can retrieve focused context while retaining a canonical complete representation and provenance. |

**Project boundaries:** mapping and refinement do not automatically implement features, change application business rules, publish tickets, or approve a graph. New automation should preserve those boundaries unless the user's task explicitly expands them.

## 5. Current workflows

### Requirement Refinement

The host's agent tools execute the investigation and judgment roles. The report
validator checks schema `1.0`; `scripts/review_workflow.py` freezes shared context,
checks role artifacts, and produces the final report plus a review audit.

1. **Frame the change.** Preserve the requirement's wording. Extract actors, triggers, entities, transitions, outcomes, constraints, and scope. Keep omitted decisions as questions.
2. **Choose the evidence mode.** Record repository-backed, documentation-only, or requirement-only analysis and the inspected revision when available.
3. **Build an evidence packet.** Read business sources, then relevant implementation, schemas, contracts, tests, UI states, readers, writers, producers, and consumers. Record precise evidence IDs and locations.
4. **Expand the impact neighborhood.** Reuse an available graph or construct a temporary map. For each connection, explain how this particular change could affect an assumption or transition.
5. **Launch two isolated investigators.** Freeze a shared packet with no initial findings. The business investigator covers lifecycle, eligibility, policy, UX, invariants, and inverse operations. The failure investigator covers concurrency, duplication, ordering, partial success, retry, recovery, compatibility, and rollout. Both may inspect authorized sources; neither reads its peer's candidates.
6. **Launch a fresh judge after both finish.** Reopen sources; reject generic checklist items; merge questions about one underlying decision; split independent decisions. Record one explained verdict for every candidate and explicit provenance for judge-added findings.
7. **Classify, prioritize, and deliver.** Produce consistent Markdown and JSON, retain settled constraints, disclose blind spots and unexpanded dependencies, and state why analysis stopped.

Fresh contexts are the default when supported, preferably parallel for the two
investigators. Limited slots can be handled by launching them sequentially in
separate contexts. If isolation is unavailable, explicitly requested otherwise,
or an actual agent failure persists after one fresh retry, start an honestly
labeled `sequential-fallback` run. Separate passes by one agent are not independent
agents; separate contexts also do not establish statistical model independence.

See the [coordinator protocol](skills/requirement-refinement/references/multi-agent-workflow.md)
and the [business](skills/requirement-refinement/references/business-investigator.md),
[failure](skills/requirement-refinement/references/failure-investigator.md), and
[judge](skills/requirement-refinement/references/evidence-judge.md) role guides.

A useful finding has a concrete initial state and action sequence, a plausible connection to the change, and a material consequence:

- `decision`: the expected behavior is unsettled.
- `investigation`: missing or conflicting evidence blocks a conclusion; specify what to inspect.
- `regression`: an established expectation needs a check.

Priorities are `blocking`, `design`, and `follow-up`, each with an explanation. Owner suggestions are roles. There are no calibrated numerical confidence or risk scores.

The [taxonomy](skills/requirement-refinement/references/edge-case-taxonomy.md) activates relevant dimensions. It is not an instruction to produce one question for every category.

### Behavior Graph Builder

In the default local workflow, the agent initializes a separate review directory,
retains its `connection.json`, and starts the packaged server before lengthy
investigation. Configured stores can instead reuse an initialized graph/review or
be provisioned explicitly through the document authoring commands below. The
server serves the released `assets/review-app/dist/`; the skill never generates a
frontend, installs Node packages, or builds the app during use. The agent authors
a proposal from available evidence, validates it, and submits it to the selected
review. The runtime does not extract the graph from code.

The review cycle is:

1. Present scope, the proposal, and material uncertainties.
2. Save browser or chat annotations against the graph, a node, a dependency, or an individual claim.
3. Read pending feedback revisions through the saved connection and respond to submitted feedback with an explanation or a revised proposal. Include changed tombstones when checking withdrawals.
4. Give each note a visible disposition: `addressed`, `accepted_gap`, or `rejected`, with a reason. Acceptance of a gap requires actual user agreement.
5. If graph content changed, submit a new round. After saving the outcome, acknowledge only each exact handled feedback revision. Refresh the inbox because changed targets can reopen notes. If only explanations were needed, use `complete-review` to clear the request once all notes are dispositioned, without inventing another graph round.
6. Leave an unresolved human question open even when its processing outcome is `needs_clarification`; obtain actual user approval only after the complete current proposal, including its listed limitations, is settled.
7. For a local session, export through the guarded command and verify the output. Configured document reviews do not yet have a generic final-artifact exporter; retain the settled review and report that capability boundary rather than bypassing export gates.

A new proposal retains annotations and snapshots. It reopens resolved notes whose directly referenced target changed or disappeared; graph-level notes reopen when graph content changes. An identical proposal does not reopen them. New proposals and semantic feedback changes invalidate earlier approval.

Reviewers can edit annotation text/kind and delete notes. Editing preserves the
original identity/target/source and reopens settled feedback; deletion records a
tombstone and its prior value in audit. Deleted notes are hidden from active
review and do not reopen on a new proposal. Both operations invalidate previous
approval. CLI `edit-annotation` and `delete-annotation` use the same domain rules.

Approval requires no open notes and no pending review request. It records the round, graph digest, and review-state digest. Export rechecks these and the expected state version. The agent must not approve on the user's behalf. Recorded chat text and a conversation reference document approval; they do not cryptographically authenticate a human identity.

### Live feedback and chat continuation

The viewer uses injected `GraphRepository` and `HumanReviewRepository` ports. Its
default HTTP adapters use the packaged API; server configuration selects the
authoritative graph and review stores. The default session adapter saves to the
same SQLite session used by the CLI. The viewer does not invoke an LLM. The
graph selector lists initialized sessions when launched with `serve --workspace`;
`serve --session` remains a catalog of one. `serve --config PATH` uses named
repository bindings, while trusted factories remain an extension mechanism.

Review responses exclude graph content and reference a graph ID and immutable
revision. Every historical round also carries its own `graph_revision`; numeric
round labels are never assumed to be storage revisions. The app reads the review
and then its exact referenced graph, rejects mismatches, and ignores late responses
after graph/round switching. Drafts and layout are scoped by graph identity.

Legacy `Store` and `DocumentReviewSession` now share `ReviewActions`. Custom
human-review adapters must implement the same conditional-write, audit, and
approval semantics; the abstraction is a protocol, not a distributed transaction
coordinator. A document proposal can publish a new immutable snapshot before its
final review compare-and-swap loses a race. In that case the catalog may advance,
but the unchanged review still points to its previous revision; inspect current
feedback before retrying. See the [repository contract](skills/behavior-knowledge-graph-builder/references/repository-contract.md).

### Configured repositories and document authoring

Server schema `behavior-review-config/1` contains `repositories`, independent
`bindings.graphs` and `bindings.reviews`, and optional `graph_id` and `client`.
Relative filesystem/database/session paths resolve against that file. Built-in
types are `filesystem`, `sqlite`, `s3`, `http`, and `session`; a legacy session
review must share the same named repository with graph reads. A trusted factory
remains available for integrations outside these built-in types.

The [configuration reference](skills/behavior-knowledge-graph-builder/references/repository-config.md)
and [example configurations](skills/behavior-knowledge-graph-builder/assets/repository-configs)
are the detailed contract. Useful starting points are
[filesystem graphs with SQLite reviews](skills/behavior-knowledge-graph-builder/assets/repository-configs/filesystem-sqlite.json),
[filesystem only](skills/behavior-knowledge-graph-builder/assets/repository-configs/filesystem.json),
[S3 graphs with SQLite reviews](skills/behavior-knowledge-graph-builder/assets/repository-configs/s3-sqlite.json),
[HTTP API](skills/behavior-knowledge-graph-builder/assets/repository-configs/http.json),
[legacy session](skills/behavior-knowledge-graph-builder/assets/repository-configs/session.json),
and [public JSON graph reads with HTTP reviews](skills/behavior-knowledge-graph-builder/assets/repository-configs/browser-json-http.json).

For document-backed storage, `register-graph --config PATH --graph PATH
--graph-id ID --revision REVISION` validates and publishes a graph,
`init-review --config PATH --graph-id ID` creates its review, and
`import-session --config PATH --session PATH` copies an existing local session's
history explicitly. Configured document sessions can apply subsequent proposal
rounds and the shared chat/review actions through their supported CLI/domain
capabilities. A compatible HTTP service provides graph/review/feedback transport;
its remote proposal-authoring API is a separate integration. Final graph artifact
export is still the guarded local-session workflow.

Document stores share `catalog.json` (`behavior-graph-catalog/1`), immutable
`graphs/<sha256 UTF8 graph ID>/<sha256 UTF8 revision>.json` snapshots, and
`reviews/<sha256 UTF8 graph ID>.json` (`behavior-human-review-document/1`). A review
document stores `state` and `audit` together; it references graph content by exact
identity/revision/digest rather than embedding an independently editable graph.
The storage port provides `read`, create-if-absent, and compare-and-swap using the
opaque version token returned by the same read. No failed conditional write may
partially save annotations, audit events, or processing metadata.

`boto3` is optional and loaded when S3 is selected. Credentials come from the
SDK's provider chain, with configurable bucket/prefix/region/profile/endpoint.
The object service must enforce conditional creates and replacements; the driver
does not downgrade to an unconditional overwrite if those operations are
unsupported. The HTTP adapter obtains optional private authentication from a
named environment variable, not from browser configuration.

The frontend loads `./config.json` before mounting. The server serves a sanitized
`behavior-review-client-config/1` projection at that route, using one same-origin
HTTP repository by default or the explicitly configured `client` object. Public
adapters are HTTP (graphs and reviews), JSON documents (graphs), and memory
snapshots (graphs). Memory catalogs explicitly choose the latest revision and
return detached copies. JSON catalogs refresh for latest reads; pinned history
uses the immutable key directly. Unknown or invalid configuration shows a startup
error instead of selecting a fallback store. Deployment configuration remains
outside the installed dist: the stable bundled `config.json` is authenticated by
the build manifest, while the server projects configuration without editing it.

### Agent waiting and review readiness

While the host keeps the agent active, `wait --after-version N --timeout 20` returns when review state changes or the timeout expires. The supported timeout is 0–30 seconds; zero provides a nonblocking presence pulse. Presence expires after 35 seconds and does not increment `state_version` or alter approval. `disconnect` clears it. The skill pauses after three idle waits instead of polling indefinitely.

Submitted feedback sets `review_requested`. Saving an individual note is not necessarily a signal that the reviewer has finished. Once the agent pauses, the user can return to chat to resume the existing session.

For custom connections, `pending-feedback --after-version N --timeout 20` offers
bounded polling through the same adapter, with a maximum timeout of 30 seconds.
It does not set agent presence. Publishing a proposal can clear `review_requested`
while leaving feedback revisions pending; the request flag is a readiness signal,
not the durable acknowledgment record.

If a usable viewer cannot be reached, continue in chat and explain the limitation. If the runtime itself is unavailable, preserve draft discussion and defer final export until its validation and approval checks can run. A cloud-local localhost address is not automatically reachable from the user's computer.

### Feedback discovery and processing

`scripts/graph_connection.py` loads a versioned `behavior-review-connection/1`
descriptor shared by server and agent. `init` creates a session's `connection.json`;
`serve --session` creates or reuses the same default for existing local sessions.
`connect` records a local session, a workspace catalog, configured repositories
through `--config PATH`, or an already implemented trusted factory/configuration
pair. Paths resolve against the descriptor's
directory, not the command's working directory. Retain the descriptor path and
selected graph ID in the review handoff; credentials stay in the adapter's own
secret configuration.

The agent can list the catalog with `feedback-graphs`, load the review and its
exact graph with `feedback-status`, and inspect `pending-feedback`. A per-graph
operation uses the descriptor's `graph_id` or explicit `--graph-id`; it never
silently selects the catalog's first entry. Commands load the adapter directly,
so a running viewer is not required to recover feedback. These commands also
accept `--config PATH` directly; a saved connection preserves its configuration
reference for later turns.

The optional Python `FeedbackProcessingRepository` protocol adds
`list_pending_feedback(graph_id)` and `mark_feedback_processed(...)` to a review
adapter. Existing adapters can continue serving the app without it; unsupported
processing operations must fail clearly. The packaged API now exposes
`GET /api/graphs/{id}/pending-feedback` and
`POST /api/graphs/{id}/mark-processed`, and the HTTP adapters forward those
operations. They are optional methods on the review repository, not a third
storage repository. The human UI does not automatically mark feedback processed.

Annotation processing fields are `feedback_revision`, `processed_revision`, a
derived `processing_status`, and the latest `processing_receipt`. The receipt
records `feedback_revision`, `outcome`, `summary`, `processed_at`, `processor`,
`graph_revision`, and `graph_digest`. Its processor is a caller-recorded identifier,
not authenticated identity. An older receipt remains visible when new feedback
makes the note pending again.

| Event | Effect on the feedback inbox |
| --- | --- |
| New UI/chat note | Revision 1, processed revision 0: pending |
| New agent-only note | Revision 0: no human feedback pending |
| Edit, delete, explicit reopen, or target-change automatic reopen | Advance the feedback revision and require acknowledgment again |
| Human resolution/disposition | Advance the feedback revision so the agent can inspect the decision |
| Agent resolution/disposition | Record the response without generating another feedback revision |
| Mark processed | Compare exact feedback revision and expected review version; save the outcome and audit, then advance the review version |

The pending envelope is `behavior-review-feedback/1`: graph identity/revision/digest,
review `state_version`, `review_requested`, and pending annotations, including
deleted notes. A tombstone matters when work from the withdrawn note was already
saved or underway. Human notes from older sessions without processing metadata
are exposed as pending, including resolved/deleted notes; agent-only notes start
at revision 0. Migration does not invent a receipt from an old resolution.

The outcomes `addressed`, `needs_clarification`, `no_change`, and `withdrawn`
describe work already handled. Save the graph/review change or explanation before
acknowledging its exact revision; never mark an entire freshly read inbox complete
after processing an earlier snapshot. A stale annotation revision or review version
conflicts and requires reassessment. Agent acknowledgments do not resolve notes,
clear `review_requested`, or approve the graph. Their operational fields stay out
of the semantic review digest, preserving the existing approval/settlement gates
and compatible legacy approvals.

Use one active consumer. Recovery is at-least-once: a crash after saving work but
before acknowledging it leaves pending feedback. Inspect existing outcomes,
resolutions, receipts, and proposal history before repeating the work. There is
no worker lease, multi-consumer coordination, or exactly-once guarantee. The
descriptor and inbox identify the selected store, not its authoring capabilities.
Document stores support the authoring workflow described above, while a remote
HTTP service needs a compatible authoring integration. If the selected adapter
cannot persist the required change, leave that work pending and report the
capability gap. Never acknowledge work merely because an inbox read succeeded.

## 6. Graph semantics and integration contract

### Behavior graph

The authoritative format is documented in the [runtime contract](skills/behavior-knowledge-graph-builder/references/runtime-contract.md) and enforced by `validate_graph` in [graph_contract.py](skills/behavior-knowledge-graph-builder/scripts/graph_contract.py), re-exported by [graph_model.py](skills/behavior-knowledge-graph-builder/scripts/graph_model.py) for compatibility.

The schema version is `behavior-graph/1`. Its root records contain `project`, `nodes`, `dependencies`, `evidence`, and `coverage`.

- **Nodes:** stable IDs, readable titles and summaries, domains, aliases, owner roles, and claims. Kinds: behavior, state, event, rule, requirement, interface, configuration.
- **Claims:** perspective (`intended`, `observed`, `proposed`), basis (`supported`, `inferred`, `unknown`), statement, aspect, and evidence references.
- **Dependencies:** endpoints, relationship, rationale, conditions, basis, and evidence references. Supported dependencies require evidence; dependency basis is supported or inferred.
- **Evidence:** source identity, kind, location, and summary.
- **Coverage:** checked material, blind spots, unexpanded areas, and conflicts.

Stable graph IDs use lower-case semantic identifiers. The validator checks uniqueness, allowed values, required fields, and reference integrity; it rejects unexpected fields. Consult the implementation before extending the schema.

The shared relationship vocabulary is:

`requires`, `implements`, `details`, `conflicts`, `reads`, `writes`, `emits`, `consumes`, `reverses`, `enforces`, `exposes`, `configured_by`.

The [relationship guide](skills/requirement-refinement/references/impact-relationships.md) defines every direction. For example:

- Writer → state via `writes`; reader → state via `reads`.
- Producer → event via `emits`; consumer → event via `consumes`.
- Reversal → forward operation via `reverses`.
- Dependent behavior → prerequisite via `requires`.

To discover affected consumers, traverse incoming `consumes` edges without reversing their stored meaning. Likewise, readers are found through incoming `reads`. A change explanation may therefore use edges that do not form a single forward-directed path. Conflict analysis considers both sides even though JSON records have `from` and `to`.

Conditions and invariants are recorded text. There is no symbolic execution or model checking of them.

### Refinement report and the current integration boundary

The [report schema](skills/requirement-refinement/references/output-schema.md) is version `1.0`. Its root contains `requirement`, `analysis`, `evidence`, `dependencies`, `findings`, `settled`, and `coverage`, plus `schema_version`.

Evidence, dependency, and finding IDs are report-local `E-001`, `D-001`, and `Q-001` identifiers. Dependency endpoints are descriptive strings, not references validated against a report node registry.

**Integration is currently agent-mediated.** The refinement agent reads the builder's index and relevant node/dependency records, checks supporting sources and freshness, and constructs its own report. No importer, automatic conversion, or end-to-end compatibility suite is bundled.

| Builder graph | Refinement report | Consequence for future integration |
| --- | --- | --- |
| Persistent semantic IDs | Report-local E-/D-/Q- IDs | Preserve a traceable ID mapping |
| Evidence `source_id` and multiple source snapshots | No evidence `source_id`; one analysis revision string/null | Design explicit provenance preservation |
| `human-feedback` evidence kind | That kind is not accepted | Do not silently recast human assertions as implementation proof or approved specifications |
| Dependency `conditions` | No conditions field | Preserve applicability without dropping meaning |
| Coverage `conflicts` array | No dedicated conflicts array | Represent disagreements as evidence and findings |
| Claim-level perspective and basis | Findings have basis; perspective is conveyed in statements/evidence | Keep policy, implementation, and proposals distinguishable |

Copying graph records directly into the report will fail or lose meaning. Both validators reject unexpected fields, so additions require a deliberate compatibility or versioning decision.

### Refinement review artifacts

The multi-agent workflow adds separate envelopes without changing report `1.0`:

| Artifact | Purpose |
| --- | --- |
| `refinement-review-packet/1` | Frozen common context, actual coordinator identity, execution mode, run ID, and canonical digest |
| `refinement-investigation/1` | One business or failure report bound to that packet, with its actual host actor ID |
| `refinement-judge-input/1` | Both completed investigations and their digests, assembled only after validation |
| `refinement-judgment/1` | Final report, candidate verdicts, judge additions, and source-check records |
| `refinement-review/1` | Retained packet, investigations, judgment, execution mode, and digest audit |

Each investigator's E-/D-/Q- identifiers are local. The judge uses qualified
candidate references such as `business:Q-001` and `failure:Q-001`, then creates
a coherent final evidence/dependency ledger. Every candidate receives exactly
one explained disposition; every final finding maps to candidates or an explicit
judge addition. Shared findings require merge labeling, with an explicit revision
for a candidate that also splits into multiple findings.

The helper detects stale inputs, missing verdicts, incorrect mappings, reused
declared actor IDs in isolated mode, and missing source-check records. Actor IDs
and source checks are recorded declarations, not proof of real context isolation
or source truth. The coordinator must actually launch the agents using the host.

`finalize` creates `refinement.json` and `review.json` in a new directory. This
refinement audit has a different schema and purpose from the graph builder's
file of the same name. The coordinator renders Markdown from the judge's report;
substantive changes return to the judge rather than bypassing the review.

### Exported artifacts

| File | Role |
| --- | --- |
| `graph.json` | Canonical complete graph |
| `index.json` | Compact lookup with node record paths |
| `nodes/node-<id>.json` | Individual node records for selective retrieval |
| `dependencies.json` | Typed relationships |
| `evidence.json` | Evidence records |
| `coverage.json` | Scope and known limitations |
| `review.json` | Approval, annotations, round metadata, source metadata, and audit events |
| `manifest.json` | Artifact hashes and graph identity |

This final-artifact export is the local-session workflow. Its complete past graph
snapshots remain in the session's SQLite database; the export does not reproduce
each past round as a separate full graph file. Keep the session when later review
or historical comparison is needed. Configured document stores retain immutable
snapshots and review history in their own storage, but do not yet implement this
generic final-artifact export.

## 7. Runtime and source map

| File | Responsibility |
| --- | --- |
| [builder SKILL.md](skills/behavior-knowledge-graph-builder/SKILL.md) | Agent orchestration and human review rules |
| [graph-authoring.md](skills/behavior-knowledge-graph-builder/references/graph-authoring.md) | Behavioral modeling, evidence, scope, and downstream use |
| [runtime-contract.md](skills/behavior-knowledge-graph-builder/references/runtime-contract.md) | Graph, state, API, CLI, and export contracts |
| [chat-review.md](skills/behavior-knowledge-graph-builder/references/chat-review.md) | Same-session review through chat |
| [graph_contract.py](skills/behavior-knowledge-graph-builder/scripts/graph_contract.py) | Persistence-independent graph/schema validation, canonical hashes, and feedback metadata primitives |
| [review_actions.py](skills/behavior-knowledge-graph-builder/scripts/review_actions.py) | Shared proposal, annotation, feedback, review-request, and approval transitions |
| [graph_model.py](skills/behavior-knowledge-graph-builder/scripts/graph_model.py) | Legacy SQLite state/audit, concurrency, presence, export, and compatibility re-exports |
| [graph_review.py](skills/behavior-knowledge-graph-builder/scripts/graph_review.py) | Local/configured authoring CLI, repository feedback commands, public config/API routes, and packaged assets |
| [graph_connection.py](skills/behavior-knowledge-graph-builder/scripts/graph_connection.py) | Saved connection schema, path resolution, shared repository loading, and graph selection |
| [repository_config.py](skills/behavior-knowledge-graph-builder/scripts/repository_config.py) | Strict server/public config schemas, named binding factories, relative-path resolution, and safe public projection |
| [document_repositories.py](skills/behavior-knowledge-graph-builder/scripts/document_repositories.py) | Shared document-store port, catalog/snapshot keys, immutable graph publication, state-plus-audit CAS, document authoring, and session import |
| [filesystem_repository.py](skills/behavior-knowledge-graph-builder/scripts/filesystem_repository.py) | JSON document persistence, interprocess locks, atomic publication, and conditional replacement |
| [sqlite_repository.py](skills/behavior-knowledge-graph-builder/scripts/sqlite_repository.py) | Transactional document database and version-token compare-and-swap |
| [s3_repository.py](skills/behavior-knowledge-graph-builder/scripts/s3_repository.py) | Optional SDK-backed object document storage with conditional create/replace and opaque ETags |
| [http_repository.py](skills/behavior-knowledge-graph-builder/scripts/http_repository.py) | Compatible upstream graph/review/feedback API client and private environment-token configuration |
| [review app source](apps/behavior-graph-review) | Maintained DOM/SVG viewer, injected contracts, controller, deterministic build, and frontend tests |
| [repository-config.js](apps/behavior-graph-review/src/repository-config.js), [repository-urls.js](apps/behavior-graph-review/src/repository-urls.js), and [config.json](apps/behavior-graph-review/src/config.json) | Public browser config validation, independent role bindings, URL resolution, and shipped default |
| [http-repositories.js](apps/behavior-graph-review/src/http-repositories.js), [json-graph-repository.js](apps/behavior-graph-review/src/json-graph-repository.js), [memory-graph-repository.js](apps/behavior-graph-review/src/memory-graph-repository.js), and [graph-documents.js](apps/behavior-graph-review/src/graph-documents.js) | Browser transport/read adapters and shared graph DTO checks |
| [packaged review app](skills/behavior-knowledge-graph-builder/assets/review-app/dist) | Built HTML/ES-module/CSS assets and hash manifest shipped with the skill |
| [graph_repositories.py](skills/behavior-knowledge-graph-builder/scripts/graph_repositories.py) | Graph, human-review, and optional feedback-processing ports, local catalog/adapters, and trusted factory loading |
| [repository-contract.md](skills/behavior-knowledge-graph-builder/references/repository-contract.md) | Adapter semantics, connection descriptors, revision consistency, CRUD, feedback lifecycle, and processing receipts |
| [repository-config.md](skills/behavior-knowledge-graph-builder/references/repository-config.md) and [configuration examples](skills/behavior-knowledge-graph-builder/assets/repository-configs) | Server/client schemas, adapter options, capability boundaries, startup, provisioning, and migration |
| [test_document_repositories.py](tests/test_document_repositories.py) | Filesystem/SQLite persistence, shared review-domain behavior, CAS races, pinned revisions, and import |
| [test_remote_repositories.py](tests/test_remote_repositories.py) | HTTP protocol fixtures and controlled S3 SDK clients; no live cloud verification |
| [test_repository_config.py](tests/test_repository_config.py) | Config validation, independent storage bindings, connections, CLI/API integration, and public projection |
| [frontend config tests](apps/behavior-graph-review/test/repository-config.test.js) and [graph adapter tests](apps/behavior-graph-review/test/graph-adapters.test.js) | Strict browser config, visible failure, exact revision paths, immutable copies, and catalog consistency |
| [test_graph_repositories.py](tests/test_graph_repositories.py) | Repository, CRUD, HTTP, legacy session, approval, and packaged-asset regressions |
| [test_feedback_processing.py](tests/test_feedback_processing.py) | Feedback revision lifecycle, concurrency, legacy compatibility, and processing/approval separation |
| [test_feedback_connection.py](tests/test_feedback_connection.py) | Saved connections, CLI discovery, app-to-agent pickup, custom adapters, and interrupted locator writes |
| [example-graph.json](skills/behavior-knowledge-graph-builder/assets/example-graph.json) and [example-sources.md](skills/behavior-knowledge-graph-builder/assets/example-sources.md) | Fictional runtime demonstration; never evidence about a user's application |
| [refinement SKILL.md](skills/requirement-refinement/SKILL.md) | Evidence retrieval, impact exploration, scenario generation, consolidation |
| [refinement references](skills/requirement-refinement/references) | Relationship semantics, scenario taxonomy, report schema |
| [validate-report.py](skills/requirement-refinement/scripts/validate-report.py) | Strict report structure and reference validation |
| [multi-agent-workflow.md](skills/requirement-refinement/references/multi-agent-workflow.md) | Fresh-context orchestration, artifact contracts, bounded recovery, and honest fallback |
| [business-investigator.md](skills/requirement-refinement/references/business-investigator.md), [failure-investigator.md](skills/requirement-refinement/references/failure-investigator.md), [evidence-judge.md](skills/requirement-refinement/references/evidence-judge.md) | Dedicated task guides for the three roles |
| [review_workflow.py](skills/requirement-refinement/scripts/review_workflow.py) | Frozen packets, role/judge artifact validation, candidate accounting, and final report/audit output |
| [test_refinement_review.py](tests/test_refinement_review.py) | Reproducible regression checks for the refinement artifact workflow |
| Both `agents/openai.yaml` files | Skill display and invocation metadata |

Builder runtime details that matter when modifying it:

- Python 3.10+; local files, SQLite, and HTTP use the standard library. S3 additionally requires an SDK version with conditional `PutObject` support. Node 18+ is used only to develop/build the app; there is no runtime frontend build or package install.
- The default local workflow uses a separate session directory containing `review.sqlite3` and the shared `connection.json`. Configured storage uses external `config.json` plus its selected stores; do not put mutable reviews or private deployment settings inside the installed skill.
- Legacy SQLite uses WAL, round snapshots, audit records, and optimistic `state_version` checks; semantic writes use `BEGIN IMMEDIATE`. Document-backed reviews apply the same domain transitions and save state/audit together through a backend version-token CAS.
- Server routes expose a graph catalog, immutable snapshots, separate review state, annotation CRUD, resolution/reopening, review requests, approval, pending feedback, processing acknowledgments, and sanitized public config. Legacy single-session routes remain available. Configured document authoring is supported; final export remains local-session only.
- Agent feedback commands share the configured repositories with the app and do not need its HTTP server. Processing receipts use conditional writes and audit but do not change the approved semantic review digest.
- The frontend polls full state every 3.5 seconds. Its initial layout groups node kinds into three columns; it is not a semantic-clustering or force-directed implementation.
- Unsaved note drafts live in browser session storage per graph ID; saved notes go through the review repository (SQLite by default). The separate review request signals readiness for the agent. Stale edits return a conflict, preserve text, load current state, and require deliberate retry.
- Loopback is the default. Host/Origin checks, CSRF protection, JSON-only mutations, and named routes limit access. The server does not serve arbitrary project files.
- `--allowed-origin` supports an already trusted preview proxy. It is not authentication; user accounts and public hosting are outside the current runtime.
- Browser endpoints require HTTPS except loopback HTTP. Cross-origin services need suitable CORS; a public client configuration does not confer authentication. CSP endpoint allowances derive only from explicit public client endpoints, not private backend storage definitions.
- Export stages files and publishes a new directory by rename. It refuses existing destinations. Filesystem publication and the SQLite receipt are separate operations, so crash recovery needs explicit testing.

### Small local checks

Run these from the repository root; use the available Python interpreter, such as `py -3` on Windows:

```bash
python3 skills/behavior-knowledge-graph-builder/scripts/graph_review.py --help
python3 skills/behavior-knowledge-graph-builder/scripts/graph_review.py validate --graph skills/behavior-knowledge-graph-builder/assets/example-graph.json
python3 skills/requirement-refinement/scripts/validate-report.py --help
python3 skills/requirement-refinement/scripts/review_workflow.py --help
python3 -m unittest discover -s tests -p 'test_*.py'
node --test apps/behavior-graph-review/test/*.test.js
node apps/behavior-graph-review/build.mjs --check
```

Validate a newly produced refinement report with:

```bash
python3 skills/requirement-refinement/scripts/validate-report.py /absolute/path/to/refinement.json
```

For a viewer check, initialize a new temporary session and serve it using the commands in the builder skill. Submit only the bundled fictional example or another explicit fixture. Do not manufacture approval in a real project session to exercise export. Do not assume an environment-specific local path, port, process handle, or preview URL from an earlier chat is still usable.

## 8. Verification status and known limits

### Evidence available at the baseline

| Status | What is established |
| --- | --- |
| Inspectable in this repository | Both workflows, strict validators, builder runtime/frontend, a fictional graph fixture, and the refinement role/artifact workflow with regression tests |
| Checked again while preparing this handoff | Source layout and contracts, Python/JavaScript syntax, and validation of the bundled graph |
| Checked for the investigator/judge update | All 22 refinement artifact tests passed, including the complete CLI sequence and compatibility with the existing report validator; stale-input, role, candidate-accounting, source-check, fallback, and output-preservation gates |
| Live refinement trial, 2026-11-20 | A fresh coordinator launched distinct business, failure, and judge contexts against a fixed seven-file fictional booking project. The investigators produced 13 and 14 candidates; the judge accounted for all 27 and consolidated them into 18 findings: 6 decisions, 7 investigations, and 5 regressions. Finalization and the existing report validator passed; all seven source files remained unchanged. |
| Reported by the original development session | Isolated checks for malformed input, reference integrity, restart persistence, concurrent edits, stale writes, annotation reopening, approval invalidation, export gates/manifests, HTTP guards, and nonsemantic presence |
| Reported by the original development session | A small fictional booking/cancellation trial produced an initial proposal with open questions and stopped before final export |
| Added for the prebuilt app/repository update | 19 backend repository/HTTP tests and 21 frontend controller/adapter/build tests passed, together with the 22 existing refinement tests. Exact distribution checks passed for both copies. An optional browser smoke harness is committed but could not run here. |
| Live packaged-builder invocation, 2026-11-21 | A fresh agent initialized a session, launched the shipped server, and saved a valid first proposal of 20 nodes and 22 relationships from seven unchanged fictional source files. It used chat presentation and ran no frontend generation, package installation, or build. Approval stayed empty and no final export occurred. Separate health requests could not reach the long-running local process in this environment, so no usable preview URL was claimed. |
| Feedback discovery/processing update, 2026-11-21 | All 68 Python tests and 26 frontend tests passed. This adds 17 lifecycle, 10 connection/CLI/integration, and 5 frontend processing cases. Checks include app HTTP save → CLI pickup/receipt → app read and edit, opaque custom-adapter revisions with SQLite forbidden, interrupted connection writes, pending tombstones, stale acknowledgments, legacy audit recovery, and unchanged approval/export compatibility. Exact distribution checks passed for both copies, and the installed skill validator passed. |
| Fresh-context feedback resume, 2026-11-21 | An agent resumed a fictional booking review through its saved connection, inspected the policy source and audit, recorded a `needs_clarification` outcome for an open policy question and `withdrawn` for a deleted request. Both exact revisions left the inbox. The human question stayed open, the withdrawal stayed deleted, graph round/digest stayed unchanged, and approval/finalization remained empty. No browser or server was needed. This was a workflow check, not a benchmark or real project approval. |
| Repository configuration frontend, 2026-11-21 | All 44 frontend tests passed, and syntax checks passed for source/build/test JavaScript. New checks cover independent bindings, URL prefixes, strict errors without fallback, visible failed startup, JSON snapshot keys and history, detached memory snapshots, feedback HTTP forwarding, and the stable config asset's manifest integrity. No visual validation was performed. |
| Repository configuration backend/release, 2026-11-21 | All 118 Python tests and 44 frontend tests passed. The distribution was rebuilt and exact manifest/source checks passed for both repository and installed skill copies; the installed skill validator passed. New suites cover 17 document/storage cases, 17 remote adapter cases, and 16 configuration/CLI/API cases, including source-selection boundaries, independently chosen storage, stale proposals and writes, pinned revisions, and credential separation. S3 uses a controlled protocol client; no live bucket or browser visual check was performed. |
| Fresh configured-review continuation, 2026-11-21 | A fresh agent used the installed skill and a supplied config binding filesystem graphs to SQLite reviews, with an opaque revision. It resumed one submitted fictional question, inspected the policy, saved a needs_clarification receipt for the exact revision, and retained the open discussion and review request. Root reads verified the empty processing inbox, unchanged graph revision/digest, null approval/finalization, and saved connection/handoff. It also reported a graph/scope mismatch rather than inventing coverage. No viewer, duplicate session, cloud write, or final export occurred. |
| S3 verification boundary | Tests use controlled SDK clients for object-key layout, ETags, conditional requests, service errors, and unsupported preconditions. No live S3 bucket or provider credentials were used; SDK/service compatibility remains a deployment integration check. |
| Data migration boundary | This release changes code, examples, and packaged assets. No live user graph/review data was migrated; session import and other storage writes were exercised only against isolated fixtures. |
| Not established | Complete builder state-machine/export coverage or CI; a successful live browser walkthrough; benchmark precision/recall or exhaustive edge-case coverage; automatic graph-to-report interoperability |

The historical builder checks and booking trial were development exercises. Their
temporary harnesses and fixtures are not committed here. The newly committed
refinement tests cover artifact mechanics; they do not replace a behavioral
evaluation corpus or establish that agents reason accurately.

The separate live refinement trial exercised actual host subagent launches rather
than fixture actor declarations. The judge recorded seven reopened source checks;
its candidate dispositions were 3 accept, 5 revise, 19 merge, and 0 reject. That
trial's project and reports were temporary development artifacts, not a committed
benchmark. It demonstrates one successful end-to-end invocation, not precision,
recall, exhaustive coverage, or source-check authentication by the validator.

The original browser walkthrough was blocked by the available environment's reachability and browser restrictions. The 2026-11-21 browser attempt was also blocked: local Chromium could not create required sockets, and the supported cloud browser could not reach the local server and then denied observation under its URL policy. No security restrictions were bypassed. Static JavaScript, controller, and HTTP tests do not establish visual layout, usability, keyboard operation, or a working private-preview deployment. The optional browser smoke harness remains unverified in this environment.

### Limits to preserve honestly

- **Schema validity is structural.** Validators do not prove source truth, evidence relevance, policy authority, freshness, completeness, or Markdown/JSON agreement.
- **Freshness is manual.** Source revisions are recorded metadata; no watcher or incremental extractor keeps the graph synchronized with code.
- **Annotation reopening is target-local.** An evidence or source-revision change may leave a referring claim object unchanged, so its resolved note will not automatically reopen on that basis alone.
- **Approval records are not identity infrastructure.** Chat approval authenticity depends on the agent faithfully recording the actual user statement.
- **Export is not a cross-filesystem/database atomic transaction.** A crash after directory publication but before the database commit can leave files without a receipt; a crash can also leave an export lock.
- **Scale is unmeasured.** Whole-state polling, whole-state JSON storage, and the current layout may become costly or crowded. Per-node export helps consumers but does not provide paginated/subgraph runtime queries.
- **External boundaries remain uncertain.** A producer's code cannot establish an inaccessible consumer's behavior.
- **No autonomous background model is bundled.** Presence only indicates an active agent wait. Server availability follows the host process and environment lifetime.
- **Feedback processing uses one active consumer.** Conditional acknowledgments prevent stale receipts, but do not claim exclusive work or prevent duplicate reasoning. Recovery may repeat work saved before an interrupted acknowledgment.
- **Authoring and final export have different coverage.** Document repositories support registration, initialization, proposal rounds, and session import through the shared domain. Remote HTTP authoring needs an explicit integration; generic final artifact export for configured stores is not implemented.
- **Built-in S3 support is not a live-provider certification.** `boto3` is optional, and the selected service must enforce create-if-absent and exact-version replacement. Controlled-client tests do not establish credentials, IAM policy, SDK version, provider-specific compatibility, or deployment availability.
- **Storage operations do not form one distributed transaction.** A failed review CAS can leave a newly published immutable graph or advanced catalog without changing the review's pinned revision. Review state/audit/receipts remain atomic together, and retry must inspect current feedback rather than assume the proposal took effect.
- **Legacy session storage is not independently bindable for review writes.** Session review and graph bindings must name the same entry; independent storage composition requires the document-backed review domain or a compatible custom/API integration.

## 9. Suggested next work

These are proposed continuation tasks, not already implemented features or instructions to expand every task at once.

### Priority 1: make current behavior reproducible

1. **Extend regression coverage and add CI.** Refinement artifact gates, builder repository/CRUD/HTTP boundaries, and frontend coordination/build now have committed tests. Extend builder coverage for complete proposal/approval/export state-machine behavior, restart persistence, concurrent operations, heartbeat isolation, and export hashes. Run the optional browser smoke harness in a supported environment. Use isolated fixtures rather than user approval records.
2. **Exercise the real browser workflow.** Verify a supported local/private-preview launch, readable graph and list views, annotations, stale-edit recovery, review requests, approval, responsive layout, keyboard interaction, and chat resumption.
3. **Test export crash recovery.** Simulate interruption around publication and receipt persistence. Define how to recover an orphaned output directory or stale lock without overwriting a valid export.

Success means a new contributor can reproduce the relevant checks from a clean checkout and can distinguish a failed gate from an environmental limitation.

### Priority 2: make reuse reliable

4. **Specify and test the graph-to-refinement adapter.** Preserve graph identity, evidence locations and revisions, conditional applicability, human feedback, and report-local IDs. Decide whether mapping fits the existing report schema or needs an explicit new version.
5. **Define source-change invalidation.** Decide what source/evidence edits mark claims stale, reopen review, or require renewed investigation. Link source changes to affected claims instead of relying solely on direct target-object differences.
6. **Build a small reviewed evaluation corpus.** Include shared-state readers, event consumers, partial cancellation/refund, approved rules that should become regression checks, contradictory documents/code, missing external contracts, and requirement-only input.

Evaluate missed consequential cases, unsupported findings, correct classification, traceable evidence, and human usefulness. Separate behavioral quality from JSON validity. Measure token/time cost if comparing retrieval strategies. Do not reuse the DRILS paper's metrics as a proxy.

### Priority 3: add complexity only against a demonstrated need

7. Measure graph size, review latency, and context cost before introducing subgraph APIs, incremental extraction, semantic search, or richer graph diff views.
8. Evaluate clustering or semantic-plus-structural retrieval against the current focused-search baseline. Domain grouping is not DBSCAN, and a promising research result does not establish the right parameters for this application.
9. Validate the included S3/HTTP adapters against an explicitly authorized deployment before adding more storage drivers. Add generic configured-store final export or HTTP authoring only with the same approval, provenance, conditional-write, and recovery guarantees. Consider additional evidence adapters or code indexes when they improve a measured workflow.

For any schema or state-machine change, update the validator, contract, agent instructions, example data, frontend/export consumers, and regression checks together. Do not add fields that downstream strict validators silently cannot accept.

## 10. Prompt for the next session

> Work in this repository and read HANDOFF.md, both SKILL.md files, and the contracts relevant to the requested change. Inspect the current implementation before assuming the handoff is up to date. Explain the concrete problem, choose one bounded improvement, and preserve evidence provenance, intended/observed/proposed distinctions, shared browser/chat review state, and approval of the exact settled graph. Treat research ideas and historical test reports as context, not implemented or freshly verified behavior. Make the requested change, run the checks that address its actual risks, update affected contracts/examples, and report what changed and what remains unverified.

This handoff should be maintained when a design decision changes, a limitation is resolved, an integration becomes executable, or new evaluation evidence becomes available.
