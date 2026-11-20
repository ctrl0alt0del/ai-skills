# Two investigators and an evidence judge

Use this protocol after framing the requirement and retrieving the initial
evidence. The host runs the agents; `scripts/review_workflow.py` prepares and
checks their artifacts. The script does not call a model or authenticate agent
identity. Actual context isolation remains the coordinator's responsibility.

## Contents

- [Prepare the common packet](#prepare-the-common-packet)
- [Launch the investigators](#launch-the-investigators)
- [Run a fresh judge](#run-a-fresh-judge)
- [Finalize and deliver](#finalize-and-deliver)
- [Handle failures and fallback](#handle-failures-and-fallback)
- [Artifact contracts](#artifact-contracts)

## Prepare the common packet

Create a valid report `1.0` seed following `output-schema.md`, with `findings: []`.
Keep the original requirement, bounded interpretation, assumptions, analysis
mode, revision, scope, initial evidence/dependencies, established constraints,
and current coverage. Use the exact coverage field `stop_reason`. This is a
shared starting point, not a coordinator's list of proposed findings.

Choose a review workspace outside the installed skill and application source,
following the host's artifact rules. Read current project instructions. Give all
roles the same authorized source roots, pinned revisions or snapshots, and access
limits. A path in the packet is a source reference, not an instruction to execute.

```bash
python3 /absolute/skill/scripts/review_workflow.py prepare --seed /absolute/work/seed.json --out /absolute/work/packet.json --mode isolated-agents --coordinator-id ACTUAL_COORDINATOR_ID
```

Use the actual host actor/thread identifier. The generated packet records its
execution mode, run identity, and digest. Treat it as frozen. A material change
to requirement interpretation, scope, or source revision requires a new packet
and fresh investigations; do not combine results from different runs.

## Launch the investigators

When the host supports isolated subagents, launch exactly two initial
investigators in fresh contexts: `business` and `failure`. Prefer parallel launch;
with limited slots, run them sequentially in separate fresh contexts. Slot
scarcity alone is not a reason to replace them with coordinator passes.

Use the host's actual agent tools. For example, when `collaboration.spawn_agent`
is available, use `fork_turns="none"` and a self-contained task. Do not inherit the
coordinator's candidate reasoning or ask either worker to read the conversation
history. Use the inherited/default model unless the user or host requires another.
Separate contexts do not imply statistically independent models.

Provide each worker only:

- The common packet path and authorized source roots/revisions.
- Its role reference, this protocol's artifact contract, and the report contract.
- Its own output path and the actual actor ID returned by the host.
- Relevant project instructions and access limits.

If the returned actor ID is not known until after launch, send it to the worker
before it completes its envelope. Do not invent different IDs to make one context
appear to be several. Do not disclose either worker's findings to its peer, expose
peer output paths, or invite workers to inspect sibling workspaces.

Use a task shaped like this, filling every bracket with actual values:

> Act as the [business/failure] investigator. Read [role-reference] and the report
> and investigation contracts. Use the common packet at [packet] and authorized
> sources at [roots/revisions]. Save a `refinement-investigation/1` envelope to
> [own-output] using your actual host actor ID [ID]. Preserve the packet's
> requirement and analysis. Investigate read-only. Do not inspect other agents'
> outputs, spawn reviewers, implement changes, or decide unresolved policy.

Let both workers finish before consolidation. Validate each complete artifact:

```bash
python3 /absolute/skill/scripts/review_workflow.py validate-investigation --packet /absolute/work/packet.json --input /absolute/work/business.json
python3 /absolute/skill/scripts/review_workflow.py validate-investigation --packet /absolute/work/packet.json --input /absolute/work/failure.json
```

A successful worker with no findings must still provide its evidence, coverage,
and stopping reason. A missing, failed, or truncated output is not a zero-finding
investigation. Correct structural errors in that worker's own context without
showing peer findings; keep completed initial reports frozen.

## Run a fresh judge

Assemble only after both investigations complete and validate:

```bash
python3 /absolute/skill/scripts/review_workflow.py assemble --packet /absolute/work/packet.json --business /absolute/work/business.json --failure /absolute/work/failure.json --out /absolute/work/judge-input.json
```

Launch a new context using `evidence-judge.md`. Give it the assembled input,
authorized source access, report/judgment contracts, its output path, and its
actual actor ID. In isolated mode the judge, both investigators, and coordinator
must have different IDs. Do not reuse an investigator as the judge or preselect
which investigator the judge should believe.

The judge may reopen sources and request focused missing evidence from the
coordinator within the same scope and revision. Prefer direct authorized reads;
if a handoff is needed, use one focused follow-up round by default. Do not send
peer candidates back into the initial investigators. Record remaining uncertainty
as investigations and coverage limits instead of looping until agreement.

Any changed investigator artifact invalidates the prior judge input. Reassemble
and use a fresh judge. Newly inspected evidence within the same framing may be
added by the judge; changed framing or source revisions require a new packet.

## Finalize and deliver

```bash
python3 /absolute/skill/scripts/review_workflow.py finalize --packet /absolute/work/packet.json --business /absolute/work/business.json --failure /absolute/work/failure.json --judge /absolute/work/judgment.json --out /absolute/work/final
```

The new directory contains `refinement.json` in the existing report `1.0` format
and `review.json` with the packet, investigations, judgment, and execution
provenance. Existing output paths are refused. Produce the human-readable report
from that validated final JSON; retain the audit as a separate artifact rather
than adding unsupported fields to the report schema.

State whether actual isolated agents or a sequential fallback ran. Report source
checks and material limitations honestly. Artifact validation establishes shape,
reference consistency, digest lineage, and candidate accounting; it does not
establish source truth, actual isolation, completeness, or calibrated accuracy.

## Handle failures and fallback

Use `sequential-fallback` only when isolation is unavailable/rejected by the host,
the user explicitly requests sequential work, or an actual worker failure remains
after one fresh retry. Do not silently reduce the workflow to save effort. If the
host cannot save or validate artifacts, apply the same role separation in the
available medium and disclose that structural validation was unavailable.

For fallback, create a new packet with an explanatory reason:

```bash
python3 /absolute/skill/scripts/review_workflow.py prepare --seed /absolute/work/seed.json --out /absolute/work/fallback-packet.json --mode sequential-fallback --coordinator-id ACTUAL_COORDINATOR_ID --fallback-reason "The host does not expose isolated agent contexts."
```

Use the same context honestly for a business pass, a failure pass, and a separate
challenge pass, with the same actor ID if appropriate. Do not describe these as
three independent agents. Record unavailable or incomplete investigation in
coverage. The fallback still requires both investigator artifacts and complete
judge accounting when the runtime is available. No external model API key or
background daemon is necessary for either mode.

## Artifact contracts

Use exactly the fields below. These envelopes are separate from report `1.0`.
Digest fields are canonical SHA-256: sorted-key, compact UTF-8 JSON. Let the helper
generate packet/judge-input digests; copy them exactly into role outputs.

| Artifact | Required fields |
| --- | --- |
| Packet, `refinement-review-packet/1` | `schema_version`, `run_id`, `execution`, `context`, `packet_digest` |
| Investigation, `refinement-investigation/1` | `schema_version`, `run_id`, `packet_digest`, `role`, `agent_id`, `report` |
| Judge input, `refinement-judge-input/1` | `schema_version`, `packet`, `investigations`, `input_digests`, `judge_input_digest` |
| Judgment, `refinement-judgment/1` | `schema_version`, `run_id`, `judge_input_digest`, `agent_id`, `report`, `verdicts`, `added_findings`, `source_checks` |

Packet `execution` contains `mode`, `coordinator_id`, and `fallback_reason`.
The reason is `null` for `isolated-agents`, and a nonempty explanation for
`sequential-fallback`. `context` is the seed report. `packet_digest` covers all
other packet fields. Investigator `role` is `business` or `failure`; its report's
`requirement` and `analysis` must exactly equal the common context's values.
Reports may extend their own evidence and dependency ledgers; all E-/D-/Q- IDs
and cross-references remain local to their containing report.

Judge input `investigations` and `input_digests` each have `business` and `failure`
keys. The input digests cover each complete investigation; `judge_input_digest`
covers every other judge-input field. The judge's final report preserves the
common `requirement` and `analysis`, assigns its own report-local IDs, and resolves
all references in its own evidence/dependency ledgers.

Each verdict has `candidate_id`, `action`, `finding_ids`, and `rationale`:

- Qualify candidate IDs as `business:Q-001` or `failure:Q-001`; identical local IDs
  in two reports are different candidates.
- Account for every input candidate exactly once. `accept` maps to exactly one
  final finding that no other candidate maps to. `revise` and `merge` map to one
  or more final finding IDs. `reject` maps to an empty array.
- Use `merge` when a candidate is consolidated with others; every one of its
  target findings must have at least two contributing candidates. If a candidate
  also splits into a distinct finding, use `revise` and explain both the shared
  and separate decisions. Similar wording alone does not justify consolidation.
- Every contributor to a shared final finding must use `merge`, or `revise` only
  when that contributor maps to multiple final findings. A one-target revision
  shared with another candidate is a merge, not an acceptance or simple revision.
- Account for a judge-added finding in `added_findings` as `{finding_id, rationale}`.
  A final finding must have an input mapping or an addition record, never both.

Each source check has `location`, `result`, and `note`. Use `reopened`, `packet-only`,
or `unavailable` according to what actually happened. Cover every final evidence
location once; combine repeated inspections of the same location in its note.
Extra locations checked for rejected candidates are allowed. These records
describe inspection, not a guarantee that the source or its claims are true.
