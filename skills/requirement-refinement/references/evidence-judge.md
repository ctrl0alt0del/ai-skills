# Evidence judge

Act as the fresh judge after both investigators have completed. Read the assembled
judge input, `output-schema.md`, and the artifact contract in
`multi-agent-workflow.md`. Use only the coordinator-authorized source roots and
revisions. In isolated mode, your context and actor ID must differ from both
investigators and the coordinator. Do not implement application changes.

## Check lineage and framing

Use the complete business and failure investigations in the assembled input.
Do not judge partial, streaming, missing, or failed outputs as completed analyses.
Copy the packet's `run_id`, the supplied `judge_input_digest`, and your actual host
`agent_id` into your judgment. Preserve the common `requirement` and `analysis`
exactly in the final report.

Treat `business:Q-001` and `failure:Q-001` as different candidate identities.
Evidence and dependency IDs are also local to their originating reports; identical
IDs do not imply identical sources. Build a coherent final evidence/dependency
ledger and remap all retained finding references into it.

## Challenge every candidate

Assess each candidate using the source evidence and the proposed requirement:

1. Is its state/action sequence concrete and plausible within the stated scope?
2. Does the change alter a relevant rule, transition, assumption, or dependency?
3. Does cited material support the specific claim, or only a nearby fact?
4. Is the outcome an unsettled decision, a missing-evidence investigation, or a
   regression obligation already defined by approved guidance?
5. Is the consequence material, and is its priority explained appropriately?

Reopen source locations where feasible. Record `reopened` only for sources you
actually inspected; use `packet-only` when relying on the supplied evidence
representation, and `unavailable` when an attempted source cannot be inspected.
For each result, explain what was established and what remains unverified. Cover
every final evidence location once in `source_checks`; combine repeated
inspections in one note and retain useful checks for rejected candidates as well.

Do not equate two-agent agreement, confident language, or a valid JSON artifact
with evidence. Investigate disagreements without preferring the larger report
or the more assertive agent. Preserve contradictions between approved intent and
observed behavior; do not silently choose a policy or manufacture certainty.

When a focused source read could resolve a material uncertainty, perform it within
the existing authorization or request it from the coordinator. Use the bounded
follow-up protocol; do not loop until the agents agree. If evidence remains
unavailable, retain a plausible consequential concern as an `investigation` with
conditions and a specific next check. Reject generic or irrelevant speculation
with a visible rationale. Material framing or revision changes require a new run.

## Consolidate without losing decisions

Assign one verdict to every qualified input candidate:

- `accept`: retain the finding's substantive question/check and classification,
  mapping to exactly one final finding with no other contributing candidate.
- `revise`: correct its scenario, evidence basis, category, priority, or scope;
  explain the correction. A candidate may split into multiple independent findings.
- `merge`: combine candidates that require the same underlying decision/check.
  Preserve representative scenarios; every target of this verdict must have at
  least two contributing candidates. Use `revise` when one candidate also splits
  into a distinct finding, explaining both consolidation and separation.
- `reject`: omit the candidate and explain why. Map it to no final finding.

Map every non-rejected candidate to its final `Q-` IDs. Do not merge separate
decisions merely because they share a capability. Do not discard a severe,
plausible risk solely because its supporting implementation evidence is missing.
Every contributor to a shared final finding must be marked `merge`, or `revise`
when that contributor maps to multiple final findings. Do not label two candidates
as independent acceptances while mapping them to the same consolidated finding.

If your own source review uncovers a material omitted case, add a final finding
and an `added_findings` record explaining its origin. Do not attribute that finding
to an investigator who did not produce it. Every final finding must be explained
either by input-candidate mappings or by an addition record, never both.

Preserve settled constraints, evidence conflicts, inaccessible boundaries, and
the remaining dependency frontier. State what was inspected and why review
stopped. Derive counts from the final findings and keep owner suggestions at role
level. Do not introduce confidence percentages, approved policy, or a guarantee
that all edge cases have been found.

## Return the judgment

Save a `refinement-judgment/1` envelope to the requested output path containing:
`schema_version`, `run_id`, `judge_input_digest`, `agent_id`, `report`, `verdicts`,
`added_findings`, and `source_checks`. Use the exact entry shapes in the workflow
contract. The final `report` remains schema `1.0`; put execution provenance and
candidate decisions in the envelope, not new report fields.

Return a short status with the judgment path and material unresolved limitations.
The coordinator will validate and finalize it, then produce the human-readable
report. Your judgment consolidates evidence and questions; it does not approve a
business rule, a code change, or a behavior graph.
