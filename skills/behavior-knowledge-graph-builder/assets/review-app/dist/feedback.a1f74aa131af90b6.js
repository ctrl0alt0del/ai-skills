/** Interpret operational feedback metadata without changing discussion status. */
export function feedbackState(note) {
  const human = source => source === 'ui' || source === 'chat';
  const humanTouched = note.source !== 'agent' || human(note.updated_by)
    || human(note.deleted_by) || human(note.resolution?.source);
  const validRevision = value => Number.isSafeInteger(value) && value >= 0;
  const revision = validRevision(note.feedback_revision)
    ? note.feedback_revision : humanTouched ? 1 : 0;
  const processedRevision = validRevision(note.processed_revision)
    && note.processed_revision <= revision ? note.processed_revision : 0;
  // Revisions are authoritative. A stale status string cannot hide a later edit.
  const pending = processedRevision < revision;
  const candidate = note.processing_receipt;
  const receipt = candidate && validRevision(candidate.feedback_revision)
    && candidate.feedback_revision > 0 && candidate.feedback_revision <= processedRevision
    ? candidate : null;
  return {
    revision,
    processedRevision,
    pending,
    label: pending ? 'Needs AI review' : revision === 0 ? 'Agent note' : 'AI processed',
    tone: pending ? 'amber' : revision === 0 ? 'blue' : 'teal',
    receipt,
    receiptIsCurrent: !!receipt && receipt.feedback_revision === revision,
  };
}

/** Withdrawn notes stay visible to the agent until their withdrawal is handled. */
export function pendingFeedback(notes = []) {
  return notes.filter(note => feedbackState(note).pending);
}

export function feedbackOutcome(outcome) {
  return {
    addressed: 'Handled',
    needs_clarification: 'Needs your input',
    no_change: 'Reviewed, no change needed',
    withdrawn: 'Withdrawal handled',
  }[outcome] || 'Reviewed';
}
