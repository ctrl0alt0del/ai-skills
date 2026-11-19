# Continue review in chat

Use the same session and versioned proposal whether the browser is unavailable,
the user prefers chat, or a specific ambiguity is easier to discuss in text.
Do not treat changing modality as a new review or approval.

When the viewer is reachable and the host can keep the turn active, the agent can
use short `wait` calls to receive submitted browser feedback directly. Chat is the
fallback after an idle pause, an unavailable viewer, or a user preference. On a
clean pause call `disconnect`; resume the same saved session in the next turn.

## Present a round

State the round number, bounded scope, and major source revisions. Show a compact
Mermaid diagram for one useful relationship, plus a table when the graph is too
large to show legibly. Use business names; include stable IDs where needed for
unambiguous feedback. Clearly identify omissions and direct the user to more
detail rather than claiming the small diagram depicts the whole application.

Present consequential uncertainties with sources, distinguish intended behavior
from observed implementation, and ask targeted questions. Give the user the
option to correct nodes/relationships, identify missing behavior, or approve the
current representation. Do not require more rounds after clear approval.

## Record feedback

Read current state before adding annotations. Use `annotate --source chat` with
the actual user's wording and a conversation reference. Use graph-level targets
for missing concepts and exact node/dependency/claim IDs for corrections.
If several feedback items require independent actions, record them separately.

Incorporate the response into a new proposal when it changes the graph. Link new
intended claims to human-feedback evidence with the review/chat reference. Preserve
contradictory implementation evidence. Resolve annotations only after addressing
them, or after an explicit user decision to accept a gap. Explain rejected changes
and keep that explanation visible for final review.

If feedback only needed an answer, use `complete-review` after all annotations are
dispositioned; keep the same graph round. Any feedback mutation clears approval.

## Record approval

Only record real user approval of the complete current round, including the
displayed scope and limitations. A compliment, confirmation of one fact, an old
approval, or the original request to build a graph is insufficient. If the target
version is ambiguous, show the current proposal and ask the user to settle it.

Use `approve-chat --text ... --source-ref ... --acknowledge-limits
--expect-version N`. Copy the actual approval; never synthesize it. The source
reference can identify the current user message and the reviewed round when a
platform message ID is unavailable. It must remain truthful and inspectable.

After recording approval, export the settled version without asking again. If
concurrent feedback or a revision invalidates approval, resolve the change and
return the affected current proposal to the user. Do not bypass the exporter.

## Missing runtime

If Python/server tools cannot run, continue a visibly draft review in chat. Keep
the proposal, exact feedback, and source references in host-supported durable
artifacts where available. Do not pretend annotations reached the runtime store.
Restore and validate them when runtime access returns before using its finalizer.
If the runtime remains unavailable, report that final artifact generation is
blocked; do not silently write an unvalidated final graph around the gate.
