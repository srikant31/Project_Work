import { useEffect, useRef, useState } from 'react';
import type { FormEvent } from 'react';
import { ArrowUpRight, ChevronLeft, ChevronRight, MessageSquare, RefreshCw, Search, ThumbsDown, ThumbsUp, Users, X } from 'lucide-react';
import { api, errorMessage } from '../api';
import { dateRangeError, formatUtc } from '../adminData';
import { pageRange, reviewLinkageNote, reviewRatingLabel, reviewStatusLabel } from '../reviewData';
import type { AdminConversation, AdminConversationsResult, AdminReview, AdminReviewDetail, AdminReviewsResult, AdminUser, AdminUsersResult, ReviewFeedbackFilter, ReviewStatusFilter } from '../types';
import { EmptyState, ErrorBanner, formatDate, Spinner } from './Shared';

const USER_PAGE_SIZE = 10;
const CONVERSATION_PAGE_SIZE = 20;
const REVIEW_PAGE_SIZE = 10;

function RecordedTime({ value }: { value: string | null | undefined }) {
  return value ? <time dateTime={value} title={formatUtc(value)}>{formatUtc(value)}</time> : <span>Not recorded</span>;
}

function Pagination({ label, total, offset, count, limit, loading, onPage }: { label: string; total: number; offset: number; count: number; limit: number; loading: boolean; onPage: (offset: number) => void }) {
  return <div className="review-pagination" aria-label={`${label} pagination`}>
    <span aria-live="polite">{pageRange(total, offset, count)}</span>
    <div><button type="button" className="icon-button" aria-label={`Previous ${label} page`} disabled={loading || offset === 0} onClick={() => onPage(Math.max(0, offset - limit))}><ChevronLeft size={16} /></button><button type="button" className="icon-button" aria-label={`Next ${label} page`} disabled={loading || offset + limit >= total} onClick={() => onPage(offset + limit)}><ChevronRight size={16} /></button></div>
  </div>;
}

function RatingBadge({ review }: { review: AdminReview }) {
  const rating = review.feedback?.rating;
  return <span className={`review-rating ${rating || 'unrated'}`}>{rating === 'down' ? <ThumbsDown size={12} /> : rating === 'up' ? <ThumbsUp size={12} /> : null}{reviewRatingLabel(rating)}</span>;
}

function TurnText({ review, full = false }: { review: AdminReview; full?: boolean }) {
  return <div className={`review-turn ${full ? 'full' : ''}`}>
    <div><h3>Question</h3>{review.question ? <><p className="review-text">{review.question.content}</p><span className="review-time"><RecordedTime value={review.question.created_at} /></span></> : <p className="review-missing">Exact question not recorded for this historical answer.</p>}</div>
    <div><h3>Assistant response</h3>{review.answer ? <><p className="review-text">{review.answer.content}</p><span className="review-time"><RecordedTime value={review.answer.created_at} /></span></> : <p className="review-missing">No response is explicitly linked to this question.</p>}</div>
  </div>;
}

function ReviewDetail({ detail, onClose }: { detail: AdminReviewDetail; onClose: () => void }) {
  const headingRef = useRef<HTMLHeadingElement>(null);
  const { review, context } = detail;
  const note = reviewLinkageNote(review);
  useEffect(() => { headingRef.current?.focus({ preventScroll: true }); headingRef.current?.scrollIntoView({ block: 'start' }); }, [review.review_id]);
  return <section className="panel review-detail" aria-labelledby="review-detail-title">
    <div className="panel-heading"><div><span className="eyebrow">RESPONSE INSPECTOR</span><h2 ref={headingRef} tabIndex={-1} id="review-detail-title">Question, answer & evidence</h2><p>{review.user.email} · {review.conversation_title || 'Untitled conversation'}</p></div><button className="icon-button" type="button" onClick={onClose} aria-label="Close response inspector"><X size={19} /></button></div>
    <div className="review-detail-content">
      <div className="review-badges"><span className={`review-status ${review.status}`}>{reviewStatusLabel(review)}</span><RatingBadge review={review} /></div>
      {note && <p className="review-note">{note}</p>}
      <TurnText review={review} full />
      <section className="review-detail-section"><h3>Feedback on this response</h3><dl className="review-facts"><div><dt>Owner's rating</dt><dd>{reviewRatingLabel(review.feedback?.rating)}</dd></div><div><dt>First feedback</dt><dd><RecordedTime value={review.feedback?.created_at} /></dd></div><div><dt>Last feedback update</dt><dd><RecordedTime value={review.feedback?.updated_at} /></dd></div></dl><p className="section-note">The current vote is shown. Historical vote changes and written reasons are not available. Missing historical timestamps have not been invented.</p></section>
      <section className="review-detail-section"><h3>Source categories</h3><p>{review.categories.length ? review.categories.join(' · ') : 'No source category recorded.'}</p><p className="section-note">Categories describe the source documents. They are not automatically classified discussion topics.</p></section>
      <section className="review-detail-section"><h3>Citations in the saved response <span>({review.citations.length})</span></h3>{review.citations.length ? review.citations.map((citation, index) => <article className="review-citation" key={`${citation.chunk_id}-${citation.start_char}-${index}`}><div className="review-source-heading"><strong>{citation.source_id || 'Source reference not recorded'} · {citation.doc_name || 'Document name not recorded'}</strong>{citation.doc_id && <a className="source-link" href={`/api/documents/${encodeURIComponent(citation.doc_id)}/file`} target="_blank" rel="noreferrer">Open source<ArrowUpRight size={13} /></a>}</div><p>Version {citation.version ?? 'not recorded'}{citation.page_number != null ? ` · Page ${citation.page_number}` : citation.section_heading ? ` · ${citation.section_heading}` : ''}</p><blockquote>{citation.quote || 'Quotation not recorded.'}</blockquote><span className="mono">{citation.start_char != null && citation.end_char != null ? `Source characters ${citation.start_char}–${citation.end_char}` : 'Source offsets not recorded'}</span></article>) : <p className="review-missing">No citations were saved for this response.</p>}</section>
      <section className="review-detail-section"><h3>Retained context chunks <span>({context.length})</span></h3><p className="section-note">These are the stored context links for this response, not every retrieval candidate. Availability below describes the source's current state.</p>{context.length ? context.map(chunk => <details className="review-chunk" key={chunk.chunk_id}><summary><span>#{chunk.rank} · {chunk.doc_name}</span><small>Version {chunk.version} · {chunk.category_name || 'Category not recorded'}</small></summary><dl className="review-facts"><div><dt>Current source state</dt><dd>{chunk.status || 'Unknown'} · {chunk.is_online === true ? 'Online' : chunk.is_online === false ? 'Offline' : 'Availability unknown'}</dd></div><div><dt>Similarity</dt><dd>{chunk.similarity_score == null ? 'Not recorded' : chunk.similarity_score.toFixed(4)}</dd></div><div><dt>Location</dt><dd>{chunk.page_number != null ? `Page ${chunk.page_number}` : chunk.section_heading || 'Document text'}{chunk.start_char != null && chunk.end_char != null ? ` · Characters ${chunk.start_char}–${chunk.end_char}` : ''}</dd></div></dl><p className="review-text">{chunk.text}</p><p className="mono">Chunk: {chunk.chunk_id}<br />Document: {chunk.doc_id}</p></details>) : <p className="review-missing">No retained context chunks are recorded for this response.</p>}</section>
      <section className="review-detail-section"><h3>Generation diagnostics</h3>{review.answer ? <><dl className="review-facts"><div><dt>Model</dt><dd>{review.answer.model_name || 'Not recorded'}</dd></div><div><dt>Response time</dt><dd>{review.answer.response_time_ms == null ? 'Not recorded' : `${review.answer.response_time_ms} ms`}</dd></div><div><dt>Top K</dt><dd>{review.answer.top_k_used ?? 'Not recorded'}</dd></div><div><dt>Insufficient context</dt><dd>{review.answer.insufficient_context == null ? 'Unknown' : review.answer.insufficient_context ? 'Yes' : 'No'}</dd></div><div><dt>Question linkage</dt><dd>{review.linkage === 'explicit' ? 'Explicit message reference' : 'Historical link unavailable'}</dd></div></dl><details className="review-diagnostics"><summary>Inspect saved generation metadata</summary>{review.answer.generation_metadata ? <pre>{JSON.stringify(review.answer.generation_metadata, null, 2)}</pre> : <p>Generation metadata was not recorded.</p>}</details></> : <p className="review-missing">No generated response or generation metadata is recorded.</p>}<details className="review-diagnostics"><summary>Record identifiers</summary><pre>{JSON.stringify({ user_id: review.user.user_id, conversation_id: review.conversation_id, question_id: review.question?.message_id || null, response_id: review.answer?.message_id || null }, null, 2)}</pre></details></section>
    </div>
  </section>;
}

export default function UserFeedback() {
  const [refresh, setRefresh] = useState(0);
  const [userSearchDraft, setUserSearchDraft] = useState('');
  const [userSearch, setUserSearch] = useState('');
  const [userOffset, setUserOffset] = useState(0);
  const [users, setUsers] = useState<AdminUsersResult | null>(null);
  const [usersLoading, setUsersLoading] = useState(true);
  const [usersError, setUsersError] = useState<string | null>(null);
  const [selectedUser, setSelectedUser] = useState<AdminUser | null>(null);
  const [conversationOffset, setConversationOffset] = useState(0);
  const [conversations, setConversations] = useState<AdminConversationsResult | null>(null);
  const [conversationsLoading, setConversationsLoading] = useState(false);
  const [conversationsError, setConversationsError] = useState<string | null>(null);
  const [selectedConversation, setSelectedConversation] = useState<AdminConversation | null>(null);
  const [feedbackFilter, setFeedbackFilter] = useState<ReviewFeedbackFilter>('all');
  const [statusFilter, setStatusFilter] = useState<ReviewStatusFilter>('all');
  const [searchDraft, setSearchDraft] = useState('');
  const [search, setSearch] = useState('');
  const [questionFromDraft, setQuestionFromDraft] = useState('');
  const [questionToDraft, setQuestionToDraft] = useState('');
  const [questionDates, setQuestionDates] = useState<{ from: string; to: string }>({ from: '', to: '' });
  const [dateError, setDateError] = useState<string | null>(null);
  const [reviewOffset, setReviewOffset] = useState(0);
  const [reviews, setReviews] = useState<AdminReviewsResult | null>(null);
  const [reviewsLoading, setReviewsLoading] = useState(true);
  const [reviewsError, setReviewsError] = useState<string | null>(null);
  const [detailId, setDetailId] = useState<string | null>(null);
  const [detail, setDetail] = useState<AdminReviewDetail | null>(null);
  const [detailLoading, setDetailLoading] = useState(false);
  const [detailError, setDetailError] = useState<string | null>(null);
  const inspectButton = useRef<HTMLButtonElement | null>(null);

  useEffect(() => {
    let active = true; setUsersLoading(true); setUsersError(null); setUsers(null);
    api.adminUsers(userSearch, USER_PAGE_SIZE, userOffset).then(result => { if (active) setUsers(result); }).catch(error => { if (active) setUsersError(errorMessage(error)); }).finally(() => { if (active) setUsersLoading(false); });
    return () => { active = false; };
  }, [userSearch, userOffset, refresh]);

  useEffect(() => {
    let active = true; setConversations(null); setConversationsError(null);
    if (!selectedUser) { setConversationsLoading(false); return; }
    setConversationsLoading(true);
    api.adminConversations(selectedUser.user_id, CONVERSATION_PAGE_SIZE, conversationOffset).then(result => { if (active) setConversations(result); }).catch(error => { if (active) setConversationsError(errorMessage(error)); }).finally(() => { if (active) setConversationsLoading(false); });
    return () => { active = false; };
  }, [selectedUser?.user_id, conversationOffset, refresh]);

  useEffect(() => {
    let active = true; setReviewsLoading(true); setReviewsError(null); setReviews(null); setDetailId(null); setDetail(null); setDetailError(null);
    api.adminReviews({ user_id: selectedUser?.user_id, conversation_id: selectedConversation?.conversation_id, feedback: feedbackFilter, status: statusFilter, search, question_from: questionDates.from || undefined, question_to: questionDates.to || undefined, limit: REVIEW_PAGE_SIZE, offset: reviewOffset }).then(result => { if (active) setReviews(result); }).catch(error => { if (active) setReviewsError(errorMessage(error)); }).finally(() => { if (active) setReviewsLoading(false); });
    return () => { active = false; };
  }, [selectedUser?.user_id, selectedConversation?.conversation_id, feedbackFilter, statusFilter, search, questionDates, reviewOffset, refresh]);

  useEffect(() => {
    let active = true; setDetail(null); setDetailError(null);
    if (!detailId) { setDetailLoading(false); return; }
    setDetailLoading(true);
    api.adminReview(detailId).then(result => { if (active) setDetail(result); }).catch(error => { if (active) setDetailError(errorMessage(error)); }).finally(() => { if (active) setDetailLoading(false); });
    return () => { active = false; };
  }, [detailId]);

  function selectUser(user: AdminUser | null) { setSelectedUser(user); setSelectedConversation(null); setConversationOffset(0); setReviewOffset(0); }
  function searchUsers(event: FormEvent) { event.preventDefault(); setUserOffset(0); setUserSearch(userSearchDraft.trim()); }
  function searchReviews(event: FormEvent) { event.preventDefault(); setReviewOffset(0); setSearch(searchDraft.trim()); }
  function applyQuestionDates(event: FormEvent) { event.preventDefault(); const problem = dateRangeError(questionFromDraft, questionToDraft); setDateError(problem); if (!problem) { setReviewOffset(0); setQuestionDates({ from: questionFromDraft, to: questionToDraft }); } }
  function closeDetail() { setDetailId(null); inspectButton.current?.focus(); }
  const selectedOffPage = selectedConversation && !conversations?.conversations.some(item => item.conversation_id === selectedConversation.conversation_id);

  return <>
    <div className="page-heading"><div><span className="eyebrow">WORKSPACE / USER FEEDBACK</span><h1>User feedback<span className="heading-dot">.</span></h1><p>Review saved questions, exact responses, source evidence, and user ratings.</p></div><button className="button secondary" onClick={() => setRefresh(value => value + 1)} disabled={usersLoading || reviewsLoading}><RefreshCw size={16} className={usersLoading || reviewsLoading ? 'spin' : ''} />Refresh</button></div>
    <p className="review-privacy-note">Administrator view · Ratings are user feedback, separate from the reviewed evaluation dataset. Requests that failed before a response was saved do not appear here.</p>
    <div className="review-workspace">
      <aside className="panel review-users" aria-labelledby="review-users-title">
        <div className="panel-heading"><div><h2 id="review-users-title">Users</h2><p>Select a person to review their activity.</p></div><Users size={18} /></div>
        <form className="review-user-search" onSubmit={searchUsers}><label className="sr-only" htmlFor="review-user-search">Search users</label><input id="review-user-search" type="search" placeholder="Email or department…" maxLength={200} value={userSearchDraft} onChange={event => setUserSearchDraft(event.target.value)} /><button type="submit" className="icon-button" aria-label="Search users"><Search size={16} /></button></form>
        <div className="review-user-list" aria-busy={usersLoading}><button className={`review-user-option all ${!selectedUser ? 'selected' : ''}`} onClick={() => selectUser(null)} aria-pressed={!selectedUser}><Users size={16} /><span>All users</span></button><ErrorBanner message={usersError} />{usersLoading ? <div className="review-loading"><Spinner label="Loading users…" /></div> : users?.users.length ? users.users.map(user => <button key={user.user_id} className={`review-user-option ${selectedUser?.user_id === user.user_id ? 'selected' : ''}`} onClick={() => selectUser(user)} aria-pressed={selectedUser?.user_id === user.user_id}><strong>{user.email}</strong><small>{user.role}{user.department ? ` · ${user.department}` : ''}</small><span>{user.conversation_count} conversations · {user.negative_count} thumbs down</span><small>Last activity: <RecordedTime value={user.last_activity_at} /></small></button>) : !usersError && <p className="review-missing">No users match your search.</p>}</div>
        {users && <Pagination label="users" total={users.total} offset={users.offset} count={users.users.length} limit={USER_PAGE_SIZE} loading={usersLoading} onPage={setUserOffset} />}
      </aside>
      <div className="review-main">
        <section className="panel review-controls" aria-labelledby="review-scope-title"><div className="panel-heading"><div><h2 id="review-scope-title">{selectedUser ? selectedUser.email : 'All users’ saved responses'}</h2><p>{selectedUser ? `${selectedUser.role}${selectedUser.department ? ` · ${selectedUser.department}` : ''}` : 'Choose a user to narrow the conversation list.'}</p></div>{selectedUser && <button className="text-button" onClick={() => selectUser(null)}>Clear user<X size={13} /></button>}</div>
          <div className="review-filter-content"><label htmlFor="review-conversation">Conversation<select aria-label="Conversation" id="review-conversation" disabled={!selectedUser || conversationsLoading || !!conversationsError} value={selectedConversation?.conversation_id || ''} onChange={event => { setSelectedConversation(conversations?.conversations.find(item => item.conversation_id === event.target.value) || null); setReviewOffset(0); }}><option value="">{!selectedUser ? 'Select a user to browse conversations' : conversationsLoading ? 'Loading conversations…' : 'All conversations for this user'}</option>{selectedOffPage && <option value={selectedConversation.conversation_id}>{selectedConversation.title || 'Untitled conversation'} (selected)</option>}{conversations?.conversations.map(conversation => <option key={conversation.conversation_id} value={conversation.conversation_id}>{conversation.title || 'Untitled conversation'} · {formatDate(conversation.started_at)} · {conversation.message_count} messages</option>)}</select></label>
            <ErrorBanner message={conversationsError} />{conversations && <><Pagination label="conversations" total={conversations.total} offset={conversations.offset} count={conversations.conversations.length} limit={CONVERSATION_PAGE_SIZE} loading={conversationsLoading} onPage={setConversationOffset} />{!conversations.total && <p className="section-note">This user has no saved conversations.</p>}</>}
            <div className="review-filter-row"><label htmlFor="review-rating">Feedback<select aria-label="Feedback" id="review-rating" value={feedbackFilter} onChange={event => { setFeedbackFilter(event.target.value as ReviewFeedbackFilter); setReviewOffset(0); }}><option value="all">All ratings</option><option value="down">Thumbs down</option><option value="up">Thumbs up</option><option value="unrated">Not rated</option></select></label><label htmlFor="review-status">Response outcome<select aria-label="Response outcome" id="review-status" value={statusFilter} onChange={event => { setStatusFilter(event.target.value as ReviewStatusFilter); setReviewOffset(0); }}><option value="all">All outcomes</option><option value="answered">Answered</option><option value="unanswered">Unanswered / insufficient evidence</option><option value="unknown">Unknown historical outcome</option></select></label></div>
            <form className="admin-date-form review-date-form" onSubmit={applyQuestionDates}><label htmlFor="review-question-from">Question from (UTC)<input aria-label="Question from (UTC)" id="review-question-from" type="date" value={questionFromDraft} onChange={event => setQuestionFromDraft(event.target.value)} /></label><label htmlFor="review-question-to">Question through (UTC)<input aria-label="Question through (UTC)" id="review-question-to" type="date" value={questionToDraft} onChange={event => setQuestionToDraft(event.target.value)} /></label><button className="button secondary small" type="submit" disabled={reviewsLoading}>Apply question dates</button><button className="text-button" type="button" disabled={reviewsLoading} onClick={() => { setQuestionFromDraft(''); setQuestionToDraft(''); setQuestionDates({ from: '', to: '' }); setReviewOffset(0); setDateError(null); }}>All question dates</button></form><ErrorBanner message={dateError} /><p className="admin-explanation" aria-live="polite">{questionDates.from || questionDates.to ? `Question dates: ${questionDates.from || 'all earlier dates'} through ${questionDates.to || 'latest'}. ` : ''}Date filters include whole UTC days and use the exact linked question date. Historical answers without a question link are excluded when a question date filter is active.</p>
            <form className="review-search" onSubmit={searchReviews}><label htmlFor="review-query-search">Search saved questions and answers<input id="review-query-search" type="search" maxLength={200} value={searchDraft} onChange={event => setSearchDraft(event.target.value)} placeholder="Search exact stored text…" /></label><button className="button secondary small" type="submit"><Search size={14} />Search</button></form>
          </div>
        </section>
        {detailLoading && <div className="panel review-loading" role="status"><Spinner label="Loading the exact response and context…" /></div>}
        <ErrorBanner message={detailError} />{detailError && <button className="text-button" onClick={closeDetail}>Close inspector</button>}
        {detail && <ReviewDetail detail={detail} onClose={closeDetail} />}
        <section className="review-results" aria-label="Saved question and answer reviews" aria-busy={reviewsLoading}><div className="results-heading"><div><h2>Saved turns{reviews ? ` · ${reviews.total}` : ''}</h2><p>Filters apply to the same question/response record. Unknown historical links remain visible.</p></div><MessageSquare size={18} /></div><ErrorBanner message={reviewsError} />
          {reviewsLoading ? <div className="panel review-loading"><Spinner label="Loading saved questions and responses…" /></div> : reviews?.reviews.length ? reviews.reviews.map(review => <article className="panel review-card" key={review.review_id}><header><div><strong>{review.user.email}</strong><p>{review.conversation_title || 'Untitled conversation'}</p></div><div className="review-badges"><span className={`review-status ${review.status}`}>{reviewStatusLabel(review)}</span><RatingBadge review={review} /></div></header>{reviewLinkageNote(review) && <p className="review-note">{reviewLinkageNote(review)}</p>}<TurnText review={review} /><footer><div><p>Source categories: {review.categories.length ? review.categories.join(' · ') : 'Not recorded'}</p>{review.feedback?.rating && <p>Feedback updated: <RecordedTime value={review.feedback.updated_at} /></p>}</div><button className="button secondary small" type="button" disabled={detailLoading && detailId === review.review_id} aria-expanded={detailId === review.review_id} onClick={event => { inspectButton.current = event.currentTarget; setDetailId(review.review_id); }}>Inspect response<ArrowUpRight size={13} /></button></footer></article>) : !reviewsError && <div className="panel"><EmptyState title="No saved turns match">Try a different user, conversation, rating, outcome, or search. Generation is currently optional; preview-only and failed requests are not saved as answers.</EmptyState></div>}
          {reviews && <Pagination label="reviews" total={reviews.total} offset={reviews.offset} count={reviews.reviews.length} limit={REVIEW_PAGE_SIZE} loading={reviewsLoading} onPage={setReviewOffset} />}
        </section>
      </div>
    </div>
  </>;
}
