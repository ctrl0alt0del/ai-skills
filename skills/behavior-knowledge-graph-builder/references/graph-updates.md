# Update an existing behavior graph

## Contents

- Establish the baseline
- Update from review
- Update from code changes
- Preserve evidence history
- Return a bounded change report

## Establish the baseline

Validate the supplied graph and retain an unchanged copy for comparison. Treat
its graph identity and revision as opaque input. In an app request, retain the
exact request ID, base digest, review version, and feedback revisions. A draft
built from one snapshot cannot silently apply to another.

Identify the requested area, relevant source locations, and what the graph
actually covers. Report a scope mismatch before inventing missing context. Expand
the graph when the task calls for that expansion; otherwise retain it as a visible
question. Preserve stable semantic node, claim, and dependency IDs when their
meaning continues. Code/file renames alone do not require new semantic IDs.

## Update from review

Read each assigned annotation, its target, status, and exact revision. A question
can require an answer without a graph change. A correction can establish intended
policy while remaining unverified as implementation. A deleted note is a
withdrawal: check whether prior work needs adjustment and keep its history.

Inspect supporting code/contracts for technical assertions. Record an actual
human policy decision as intended behavior with human-feedback evidence. Preserve
disagreement with observed implementation in coverage.conflicts. Do not infer
global approval or acceptance of an unknown from a local confirmation.

Return one recommendation for every assigned feedback revision:

- `addressed`: identify the actual graph change or supported answer.
- `needs_clarification`: state the concrete decision still needed; keep the
  discussion open.
- `no_change`: explain why the graph should stay as it is, with evidence.
- `withdrawn`: describe the effect of the deletion and any prior work checked.

Recommend a discussion disposition only when justified. Human acceptance of a
limitation must have an actual source; do not manufacture it to settle the graph.
The review-app coordinator owns applying dispositions and processing receipts.

## Update from code changes

1. **Identify the compared sources.** Read recorded baselines and inspect the
   actual target commit/worktree. Use the supplied range when available. For
   uncommitted changes, describe the inspected working state accurately; do not
   invent a commit. If a baseline cannot be retrieved, declare bounded reinspection
   and a freshness limitation rather than claiming a complete diff.
2. **Find changed evidence.** Inspect changed files/symbols, renamed and deleted
   code, tests, API/event contracts, migrations, configuration, and meaningful
   documentation changes. Link them to graph claims and dependencies through
   existing evidence. Also inspect newly added behavior with no old graph entry.
3. **Follow the affected behavior.** Expand both incoming and outgoing typed
   relationships. A changed writer can affect unchanged readers; a changed event
   producer can affect consumers. Check reversal/compensation, permissions,
   historical data, background jobs, and external consumers when relevant.
   Inspect enough context to establish behavior, not just changed lines.
4. **Reconcile each finding.** Keep, revise, add, or remove a claim/edge/node based
   on the evidence. Keep policy and observed behavior separate. Removal requires
   evidence that the behavior or relationship is obsolete, not merely an empty
   search result. Repair dependent references and record removals explicitly.
5. **Bound the freshness claim.** Report what was reinspected, what was preserved
   from the earlier graph, and what remains inaccessible. Stop expansion when the
   requested area is adequately supported; record consequential unexpanded branches.

For mixed updates, assess code changes and feedback against the same baseline.
Do not run two independent rewrites and let the later one erase the earlier work.

## Preserve evidence history

`project.sources[].revision` applies to every evidence entry referencing that
source ID. Therefore, changing an existing source's revision would also relabel
untouched evidence. Preserve that older source record while old evidence remains.
Add a distinct source ID for the newly inspected revision and point new/revised
evidence at it. Reuse the new record within the same inspected revision.

Keep stable claim IDs when a continuing claim is revalidated or corrected; update
its evidence references deliberately. Preserve old evidence used by unaffected
claims. Remove orphaned records only when no remaining claim/dependency needs
them, and list material removals in the change report. Human feedback and code
evidence can coexist on a claim with distinct perspectives or separate claims.

Example: a refund rule still reflects an approved specification at revision A,
while a changed handler is inspected at code revision B. Retain the intended rule
and its specification evidence. Update observed behavior with a new code source
record for B; expose a conflict if the handler violates the rule. Do not replace
the rule simply because the implementation changed.

## Return a bounded change report

Validate the resulting graph and compare it to the baseline. Report meaningful
additions, modifications, removals, affected flows, reviewed feedback outcomes,
remaining questions, and source freshness limits. Distinguish the mechanically
computed ID diff from your explanation of the changed behavior.

When nothing needs to change, return the same graph content with an explanation.
Do not force an empty proposal round by changing formatting, IDs, titles, or
source versions solely to appear productive. A result remains a proposal; applying
it and requesting human approval are responsibilities of the caller.
