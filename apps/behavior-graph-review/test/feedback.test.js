import test from 'node:test';
import assert from 'node:assert/strict';
import { feedbackState, pendingFeedback, feedbackOutcome } from '../src/feedback.js';

const humanNote = {
  id: 'note-a', source: 'ui', status: 'open', text: 'What happens when a request times out?',
};
const receipt = {
  feedback_revision: 1, outcome: 'needs_clarification', summary: 'Should retry preserve the reservation?',
  processed_at: '2026-11-21T11:00:00Z', processor: 'agent', graph_revision: 'release-a', graph_digest: 'digest-a',
};

test('a response can be processed while the discussion still needs a decision', () => {
  const note = {...humanNote, feedback_revision: 1, processed_revision: 1, processing_receipt: receipt};
  const state = feedbackState(note);
  assert.equal(state.pending, false);
  assert.equal(state.label, 'AI processed');
  assert.equal(state.receiptIsCurrent, true);
  assert.equal(feedbackOutcome(state.receipt.outcome), 'Needs your input');
  assert.equal(note.status, 'open');
});

test('a later edit stays pending even if a stale status string still says processed', () => {
  const note = {...humanNote, feedback_revision: 2, processed_revision: 1,
    processing_status: 'processed', processing_receipt: receipt};
  const state = feedbackState(note);
  assert.equal(state.pending, true);
  assert.equal(state.label, 'Needs AI review');
  assert.equal(state.receiptIsCurrent, false);
  assert.equal(state.receipt.feedback_revision, 1);
});

test('pending feedback includes withdrawals and settled discussions, but not initial agent notes', () => {
  const notes = [
    {...humanNote, status: 'deleted', feedback_revision: 2, processed_revision: 1},
    {...humanNote, id: 'note-b', status: 'resolved', feedback_revision: 1, processed_revision: 0},
    {...humanNote, id: 'note-c', feedback_revision: 1, processed_revision: 1},
    {...humanNote, id: 'note-d', source: 'agent', feedback_revision: 0, processed_revision: 0},
  ];
  assert.deepEqual(pendingFeedback(notes).map(note => note.id), ['note-a', 'note-b']);
  assert.equal(feedbackState(notes[3]).label, 'Agent note');
});

test('legacy human feedback is pending even when resolved; untouched agent notes are not', () => {
  assert.equal(feedbackState({...humanNote, status: 'resolved'}).pending, true);
  assert.equal(feedbackState({...humanNote, source: 'chat'}).pending, true);
  assert.equal(feedbackState({...humanNote, source: 'agent'}).pending, false);
  for (const touch of [{updated_by: 'ui'}, {deleted_by: 'chat'}, {resolution: {source: 'ui'}}]) {
    assert.equal(feedbackState({...humanNote, source: 'agent', ...touch}).pending, true);
  }
});

test('invalid or future processing revisions cannot hide human feedback', () => {
  for (const processed_revision of [-1, 2, 1.5, '1', NaN]) {
    const state = feedbackState({...humanNote, feedback_revision: 1, processed_revision});
    assert.equal(state.pending, true);
    assert.equal(state.receipt, null);
  }
  const state = feedbackState({...humanNote, feedback_revision: 1, processed_revision: 0, processing_receipt: receipt});
  assert.equal(state.receipt, null);
});
