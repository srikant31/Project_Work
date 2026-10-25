import type { AdminReview } from './types';

export function reviewStatusLabel(review: Pick<AdminReview, 'status' | 'answer'>) {
  if (review.status === 'answered') return 'Answered';
  if (review.status === 'unknown') return 'Outcome unknown';
  return review.answer?.insufficient_context === true ? 'Insufficient evidence' : 'No response recorded';
}

export function reviewLinkageNote(review: Pick<AdminReview, 'linkage' | 'status'>) {
  if (review.linkage === 'legacy_unlinked') return 'Historical answer: its exact question was not linked. No question has been inferred.';
  if (review.linkage === 'question_without_response' && review.status === 'unknown') return 'No explicit answer link. This conversation also has historical unlinked answers, so the outcome is unknown.';
  if (review.linkage === 'question_without_response') return 'No answer is explicitly linked to this question.';
  return null;
}

export function reviewRatingLabel(rating: 'up' | 'down' | null | undefined) {
  return rating === 'down' ? 'Thumbs down' : rating === 'up' ? 'Thumbs up' : 'Not rated';
}

export function pageRange(total: number, offset: number, count: number) {
  return count ? `${offset + 1}–${offset + count} of ${total}` : `0 of ${total}`;
}
