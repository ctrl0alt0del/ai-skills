# Author a behavior graph

## Contents

- Choose semantic units
- Capture claims and evidence
- Trace dependencies
- Handle scope and freshness
- Prepare a readable proposal
- Use the final graph for refinement

## Choose semantic units

Represent meaningful business operations or state changes as behavior nodes:
`commerce.purchase.complete`, `commerce.refund.issue`, or `account.deactivate`.
Use domain to group capabilities. Attach source symbols as evidence rather than
making every function a business node. A behavior may span several services.

Add state, event, rule, requirement, interface, and configuration nodes when they
explain dependencies. Describe data meaning and units, not just field types.
Distinguish historical totals from current balances, purchase price from funding,
and a semantic event from an implementation callback. Name versions when an event
or interface has independently supported contracts.

Store short human-readable titles and concise summaries. Use stable IDs that
survive renaming or moving code. Change an ID only when the concept itself changes.
Do not create new IDs for the same behavior in every review round.

## Capture claims and evidence

Use claim.aspect to capture actors, triggers, preconditions, state transitions,
outcomes, invariants, timing, failure/recovery guarantees, and assumptions. Keep
different claims separate when their evidence or perspective differs.

- intended: approved or stated desired business behavior; identify its authority.
- observed: behavior established by the inspected implementation, test, or trace.
- proposed: a possible change not yet part of the current baseline.
- supported: cited evidence establishes this specific claim.
- inferred: plausible interpretation; explain rather than implying verification.
- unknown: the required knowledge is missing; name the gap in coverage as well.

A handler consuming an event does not prove that all event fields affect it.
Reading a field does not establish what Product intended that field to mean.
Passing one test does not establish every concurrency or failure guarantee.

Record source IDs and inspected revisions per repository/document. Use null when
unknown; never invent a revision or approval. Retain conflicts between current
code and intended policy. A recent review date does not prove unchanged code.

Human annotations can establish intended policy or report facts. Preserve the
annotation or chat reference as human-feedback evidence and its exact scope.
Verify reported implementation facts against code when possible; retain them as
reported/inferred when not verified. Do not label user approval of the graph as
technical proof of every depicted behavior.

## Trace dependencies

Reuse these directions exactly:

| Relationship | From → to |
| --- | --- |
| requires | Dependent behavior → prerequisite |
| implements | Implementation/behavior → intended requirement |
| details | Detailed requirement → broader requirement |
| conflicts | Rule → conflicting rule; inspect both directions |
| reads | Reader → state |
| writes | Writer → state |
| emits | Producer → event |
| consumes | Consumer → event |
| reverses | Reversal/compensation → forward operation |
| enforces | Guard/behavior → invariant |
| exposes | Interface/report → behavior or state |
| configured_by | Behavior → configuration |

Inspect incoming edges too: writers lead to shared state, then incoming reads
identify readers; producers lead to events, then incoming consumes identify
consumers. Avoid generic 'related to' edges that hide the dependency's meaning.

Use rationale to explain the persistent coupling, e.g. 'The reward handler reads
the whole purchase amount to calculate progress.' Use conditions for known
configuration, cohort, lifecycle, or version restrictions. Do not invent a
requirement-specific impact conclusion in the baseline graph.

Check inverse operations, external consumers, shared authorization, UI states,
background jobs, migrations, and historical data when sources make them relevant.
Connectivity identifies a candidate dependency; it does not prove every change
propagates through that connection.

## Handle scope and freshness

Start with an application inventory and a bounded area that a human can review.
Expand consequential relationships across code/service/domain boundaries rather
than limiting knowledge to lexical similarity or an arbitrary number of hops.
Track unexpanded branches and inaccessible systems. Do not infer absence from an
empty code search or missing diagram node.

Populate coverage.checked, blind_spots, unexpanded, and conflicts honestly. Include
the limits of runtime/contract evidence. Unknowns can remain in a final graph only
as visible, explicitly accepted limitations of the map. This does not approve an
unknown policy or certify a safety invariant.

When updating an existing graph, inspect changes since the recorded sources and
flag affected claims for review. Keep baseline intent separate from new observed
code. Preserve stable unaffected IDs and accepted decisions unless new evidence
actually challenges them.

## Prepare a readable proposal

Give humans business titles, short explanations, and examples. Introduce domain
overviews first, then focus on a flow and its immediate relevant dependencies.
Large maps need search, filters, neighborhood focus, and a list alternative; tell
the user when a view hides nodes. Approval always covers the declared proposal.

For each result, summarize additions, removals, changed meaning, recommended
feedback outcomes, and remaining uncertainties. Supply these to the caller with
the graph. The review-app skill handles presentation controls, durable feedback,
approval, and subsequent rounds; the builder can also return a standalone draft.

## Use the final graph for refinement

The final index maps stable IDs and descriptions to node records. A refinement
agent finds matching operations, reads focused nodes, expands both directions,
checks claim evidence/freshness, and derives change-specific questions. It maps
persistent IDs into the report's E-/D-/Q- identifiers without losing provenance.
The graph input format and refinement report format are intentionally different.
An approved graph is a reviewed knowledge snapshot, not proof of completeness.
