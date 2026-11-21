# Continue review in chat

## Contents

- [Resume the review](#resume-the-review)
- [Present a round](#present-a-round)
- [Record feedback](#record-feedback)
- [Record approval](#record-approval)
- [Missing runtime](#missing-runtime)

## Resume the review

Use the same session and versioned proposal whether the browser is unavailable,
the user prefers chat, or a specific ambiguity is easier to discuss in text.
Do not treat changing modality as a new review or approval.

Resume through the saved repository connection, rather than guessing where the
app stores annotations. Retain its path and selected graph ID in the task handoff.
For local sessions, `init` creates `connection.json`; use `connect` for an older
session or an already configured catalog/adapter. See `repository-contract.md`.

When the viewer is reachable and the host can keep the turn active, the agent can
use short `wait` calls to receive submitted browser feedback directly. Chat is the
fallback after an idle pause, an unavailable viewer, or a user preference. On a
clean pause call `disconnect`; resume the same saved session in the next turn.
For a custom repository, bounded `pending-feedback --after-version N --timeout 20`
can wait for review changes, but does not set an agent-presence indicator.

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

Read `feedback-status --connection PATH` and `pending-feedback --connection PATH`
before adding annotations or acting on the user's new message. Select the graph
with `--graph-id ID` if it is not recorded in the descriptor. Use the pinned graph
returned by the status command. Pending feedback includes deletions and new human
decisions on resolved notes, not only open discussions.

For a local session or configured document review, use `annotate --source chat` with
the actual user's wording and a conversation reference. Use graph-level targets
for missing concepts and exact node/dependency/claim IDs for corrections.
If several feedback items require independent actions, record them separately.
Use the selected `--connection` or `--config` for supported document-review
commands. HTTP-only authoring needs an existing authorized integration. Do not
claim a chat correction was saved if persistence is unavailable.

Pass the pinned graph, exact selected feedback revisions, relevant source context,
and conversation references to Behavior Knowledge Graph Builder. Have the builder
return the revised proposal and per-item reasoning, preserving contradictory
implementation evidence. Publish its result through this app's runtime. Resolve
annotations only after addressing them, or after an explicit user decision to
accept a gap. Explain rejected changes
and keep that explanation visible for final review.

For a pending item that still needs an answer, record the exact question and leave
its discussion open. For a withdrawn note, inspect whether earlier work needs
adjustment without resurrecting the deleted request. Do not confuse processing
with resolution: a `needs_clarification` outcome may be processed and still open.

After saving the actual outcome, use `mark-processed --connection PATH --id ID
--feedback-revision REV --outcome OUTCOME --summary TEXT --processor ACTOR
--expect-version N`. Select one of `addressed`, `needs_clarification`, `no_change`,
or `withdrawn` and describe what was saved or remains unanswered. Acknowledge only
the exact inspected revision. Re-read after a conflict; never acknowledge a newer
edit, deletion, or reopening by substituting its token into an old conclusion.
Refresh the pending feed after proposing a changed graph, because changed targets
can reopen previously resolved notes.

`review_requested` signals that the reviewer submitted feedback for handling;
saving a note alone need not mean the reviewer finished. An explicit chat request
also starts processing. Pending revisions persist if a proposal clears the flag.
On interruption/resume, inspect saved outcomes, receipts, and review history before
repeating work. Use one active consumer; there is no worker lease or background AI.

If feedback only needed an answer, use `complete-review` after all annotations are
dispositioned; keep the same graph round. Semantic feedback changes clear approval.
A processing receipt is operational: it does not resolve notes, clear the request,
approve the graph, or invalidate an existing approval. If required graph/review
writes could not be saved, leave the affected revision pending and report the gap.

## Record approval

Use `edit-annotation` or `delete-annotation` only for an actual requested correction
or removal of saved feedback. Record the corrected wording faithfully and retain
the tombstone/audit. Do not remove a question merely to make the proposal settled.

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

If Python/server tools cannot run or the shipped app distribution is missing,
continue a visibly draft review in chat. Never generate or rebuild an app as a
fallback during graph review. Keep
the proposal, exact feedback, and source references in host-supported durable
artifacts where available. Do not pretend annotations reached the runtime store.
Restore and validate them when runtime access returns before using its finalizer.
If the runtime remains unavailable, report that final artifact generation is
blocked; do not silently write an unvalidated final graph around the gate.
