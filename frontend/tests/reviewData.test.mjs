import assert from 'node:assert/strict';
import { test } from 'node:test';
import { pageRange, reviewLinkageNote, reviewRatingLabel, reviewStatusLabel } from '../src/reviewData.ts';

test('saved refusals and missing responses are distinguishable without inferring generation failures', () => {
  assert.equal(reviewStatusLabel({ status: 'unanswered', answer: { insufficient_context: true } }), 'Insufficient evidence');
  assert.equal(reviewStatusLabel({ status: 'unanswered', answer: null }), 'No response recorded');
  assert.equal(reviewStatusLabel({ status: 'unknown', answer: { insufficient_context: null } }), 'Outcome unknown');
  assert.equal(reviewStatusLabel({ status: 'answered', answer: { insufficient_context: false } }), 'Answered');
});

test('historical question linkage is explicitly unknown and never paired by time', () => {
  assert.match(reviewLinkageNote({ linkage: 'legacy_unlinked', status: 'answered' }), /No question has been inferred/);
  assert.match(reviewLinkageNote({ linkage: 'question_without_response', status: 'unknown' }), /historical unlinked answers/);
  assert.match(reviewLinkageNote({ linkage: 'question_without_response', status: 'unanswered' }), /No answer is explicitly linked/);
  assert.equal(reviewLinkageNote({ linkage: 'explicit', status: 'answered' }), null);
});

test('missing feedback is not presented as a negative vote', () => {
  assert.equal(reviewRatingLabel(null), 'Not rated');
  assert.equal(reviewRatingLabel(undefined), 'Not rated');
  assert.equal(reviewRatingLabel('up'), 'Thumbs up');
  assert.equal(reviewRatingLabel('down'), 'Thumbs down');
});

test('pagination represents the actual returned window and empty results', () => {
  assert.equal(pageRange(25, 10, 10), '11–20 of 25');
  assert.equal(pageRange(25, 20, 5), '21–25 of 25');
  assert.equal(pageRange(0, 0, 0), '0 of 0');
});
