# Edge-case discovery

Activate dimensions from the requirement and observed dependencies. Do not
generate a question per row automatically. Use specific sequences that expose
unresolved outcomes or regression obligations.

| Dimension | Construct a concrete counterexample |
| --- | --- |
| Preconditions and eligibility | Eligibility holds at start, then changes before completion; an actor is only partly eligible. |
| Boundaries | Inspect zero, empty, absent versus null, minimum/maximum, threshold equality, precision, and rounding where relevant. |
| Lifecycle | Trigger an action before activation, while pending, after expiry, after deletion, or after reversal. |
| Business invariants | Find a sequence violating conservation, uniqueness, entitlement, quota, or a documented cross-feature rule. |
| Shared state | Two valid operations inspect the same resource, then both act on stale availability. |
| Time and ordering | Move expiry, cancellation, authorization, or configuration changes between steps; consider time zones where relevant. |
| Duplicates and retries | Lose a response after a committed side effect; retry with the same or different operation identity. |
| Partial success | Commit one side effect and fail the next; inspect what the user sees and how recovery proceeds. |
| Events | Deliver a duplicate, delayed, missing, out-of-order, or older-version message to a known consumer. |
| Inverse operations | Reverse part of an operation after resources expire, transfer, or are consumed; repeat reversal. |
| Authority and isolation | Revoke a role mid-flow, use another tenant's identifier, or attempt a privileged inverse action. |
| Cross-feature behavior | Change data meaning while a confirmed reward, invoice, audit, report, or other consumer assumes the old meaning. |
| Compatibility | Read legacy records with missing fields; run old clients or consumers during mixed-version rollout. |
| User experience | Refresh, navigate away, repeat an action, or receive a delayed response while an outcome is ambiguous. |
| Operations and recovery | Interrupt a batch midway, rerun it, disable a flag mid-operation, or recover a known failed side effect. |

## Relationship-specific prompts

- `requires`: vary prerequisite validity between checking and using it.
- `reads` / `writes`: identify competing writers and consumers of intermediate state.
- `emits` / `consumes`: vary schema, semantics, timing, delivery, and multiplicity.
- `reverses`: preserve allocation, provenance, eligibility, and downstream effects.
- `enforces` / `conflicts`: find newly reachable states violating existing rules.
- `exposes`: inspect observable pending, success, failure, and recovery behavior.
- `configured_by`: inspect cohort boundaries and changes during in-flight work.
- `implements` / `details`: check intended versus observed behavior and omissions.

## Finding admission

Keep a candidate if you can answer all four questions:

1. What precise state and action sequence exposes the issue?
2. How does the proposed change create or alter this issue?
3. Which outcome is unresolved, evidence is missing, or approved rule needs checking?
4. What consequence makes this worth the team's attention?

Retain consequential hypotheses as investigations when evidence is missing; name
the missing dependency or contract. Do not promote them into observed facts.

## Illustration

Requirement: allow promotional credits to fund part of a purchase.

Weak: "What about refunds?"

Useful: "A purchase used 7 promotional units and 5 cash units. A partial refund of
6 units is requested after the original promotion expires. Which funding source
is restored, and does restored credit retain its original expiry?"

This is not evidence that the user's product supports refunds or credit expiry.
Verify those features or state the conditions explicitly. Split allocation and
expiry into separate questions if they are independent decisions.

If an approved rule already answers the scenario, preserve it and report a
regression check. Do not reopen policy merely because implementation needs a new
field or mechanism.

## Consolidation

Merge candidates requiring the same decision even if wording differs. Keep
decisions separate when answers or owners can differ. Distinguish policy from
engineering mechanisms. Present options as proposals. Omit numerical likelihood
and severity unless a calibrated rubric and supporting evidence were supplied.
