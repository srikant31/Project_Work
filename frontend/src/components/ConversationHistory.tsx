import { useEffect, useState } from 'react';
import EvidenceButton from './EvidenceButton';
import NegativeFeedback from './NegativeFeedback';
import { api, errorMessage } from '../api';
import type { ConversationHistory as History, ConversationsResult } from '../types';
import { formatUtc } from '../adminData';
import { EmptyState, ErrorBanner, ReadFailure, Spinner } from './Shared';

export default function ConversationHistory({ selectedId, onSelect, onNew, onSaved, onContinue, transcriptOnly = false, revision = "", disabled = false }: {
  selectedId: string | null; onSelect: (id: string) => void; onNew: () => void; onSaved?: () => void; onContinue: (id: string) => void; transcriptOnly?: boolean; revision?: string; disabled?: boolean;
}) {
  const [list, setList] = useState<ConversationsResult | null>(null);
  const [detail, setDetail] = useState<History | null>(null);
  const [offset, setOffset] = useState(0);
  const [refresh, setRefresh] = useState(0);
  const [loading, setLoading] = useState(true);
  const [opening, setOpening] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [detailError, setDetailError] = useState<string | null>(null);
  const [negativeId, setNegativeId] = useState<string | null>(null);
  useEffect(() => { setNegativeId(null); }, [selectedId]);
  const [voting, setVoting] = useState<string | null>(null);
  useEffect(() => {
    if (transcriptOnly) return;
    let active = true; setLoading(true); setError(null);
    api.conversations(20, offset).then(value => { if (active) setList(value); })
      .catch(caught => { if (active) setError(errorMessage(caught)); })
      .finally(() => { if (active) setLoading(false); });
    return () => { active = false; };
  }, [offset, refresh, transcriptOnly]);
  useEffect(() => {
    let active = true; setDetail(null); setDetailError(null); setOpening(!!selectedId);
    if (selectedId) api.conversation(selectedId).then(value => { if (active) setDetail(value); })
      .catch(caught => { if (active) setDetailError(errorMessage(caught)); })
      .finally(() => { if (active) setOpening(false); });
    return () => { active = false; };
  }, [selectedId, refresh, revision]);
  async function vote(messageId: string, rating: 'up' | 'down', comment?: string) {
    if (!detail || !selectedId) return;
    const conversation = selectedId; setVoting(messageId); setDetailError(null);
    try {
      const result = await api.feedback(messageId, rating, comment);
      setDetail(current => current?.conversation_id === conversation ? { ...current,
        messages: current.messages.map(message => message.message_id === messageId ? { ...message, rating: result.rating, feedback_comment: result.comment } : message) } : current);
      onSaved?.();
    } catch (caught) { setDetailError(errorMessage(caught)); throw caught; }
    finally { setVoting(null); }
  }
  return <>
    {negativeId && <NegativeFeedback onCancel={() => setNegativeId(null)} onSubmit={comment => vote(negativeId, 'down', comment)} />}
    {!transcriptOnly && <div className="page-heading"><div><span className="eyebrow">WORKSPACE / HISTORY</span><h1>Conversation history</h1><p>Your saved questions, answers, sources and feedback.</p></div>
      <div className="history-actions"><button className="button secondary" disabled={loading || opening} onClick={() => setRefresh(value => value + 1)}>Refresh history</button><button className="button primary" onClick={onNew}>New conversation</button></div></div>}
    <ErrorBanner message={error} />
    <div className={transcriptOnly ? "chat-transcript" : "history-layout"}>{!transcriptOnly && <section className="panel history-list" aria-label="Your conversations">
      {loading ? <Spinner label="Loading conversations…" /> : error ? <ReadFailure title="Could not load conversations" onRetry={() => setRefresh(value => value + 1)} /> : list?.conversations.length ? <>
        {list.conversations.map(item => <button className={`history-choice ${selectedId === item.conversation_id ? 'selected' : ''}`} key={item.conversation_id}
          aria-pressed={selectedId === item.conversation_id} onClick={() => onSelect(item.conversation_id)}>
          <strong>{item.title || 'Untitled conversation'}</strong><small>{formatUtc(item.started_at)}</small></button>)}
        <div className="history-actions"><button className="button secondary small" disabled={!offset} onClick={() => setOffset(value => Math.max(0, value - 20))}>Previous conversations</button>
          <button className="button secondary small" disabled={offset + list.conversations.length >= list.total} onClick={() => setOffset(value => value + 20)}>Next conversations</button></div>
        <p className="section-note">{offset + 1}–{offset + list.conversations.length} of {list.total} conversations</p>
      </> : <EmptyState title="No saved conversations">Completed assistant turns will appear here. Context previews and failed requests are not saved chats.</EmptyState>}
    </section>}<section className="panel history-transcript" aria-label="Saved transcript" aria-busy={opening}>
      <ErrorBanner message={detailError} />
      {opening ? <Spinner label="Opening conversation…" /> : detailError ? <ReadFailure title="Could not open conversation" onRetry={() => setRefresh(value => value + 1)} /> : detail ? <>
        <h2>{detail.title || 'Untitled conversation'}</h2>{!transcriptOnly && <button className="button primary" onClick={() => onContinue(detail.conversation_id)}>Continue this conversation</button>}<p className="section-note">Times shown in UTC. Historical sources may now be offline; file access is checked separately.</p>
        {detail.messages.length ? detail.messages.map(message => <article className={`history-message ${message.role}`} key={message.message_id}>
          <header><strong>{message.role === 'user' ? 'You' : 'Assistant'}</strong><time>{formatUtc(message.created_at)}</time></header>
          <p className="history-content">{message.content}</p>
          {message.role === 'assistant' && <>
            {message.generation?.answer_status === 'clarification_required' && <p className="section-note">Clarification requested before policy retrieval.</p>}
            {message.insufficient_context === true && <p className="section-note">Insufficient evidence recorded.</p>}
            {!message.reply_to_message_id && <p className="section-note">Legacy response: exact question link was not recorded.</p>}
            {message.generation?.answer_style === 'sentence' && !!message.generation?.citations?.length && <p className="section-note">Written from the verified source quotes below. Open a source to see the exact text.</p>}
            {message.generation?.citations?.map((citation, index) => <details className="history-citation" key={index}><summary>{citation.doc_name || 'Saved source'} · Version {citation.version ?? 'unknown'} · {citation.page_number ? `Page ${citation.page_number}` : citation.section_heading || 'Document text'}</summary>
              <blockquote>{citation.quote || 'Quote not recorded'}</blockquote>{citation.chunk_id && <EvidenceButton responseId={message.message_id} chunkId={citation.chunk_id} />}</details>)}
            <div className="history-actions" aria-label="Response feedback"><span>Was this helpful?</span>
              <button className="button secondary small" aria-label={`Positive feedback for response ${message.message_id}`} aria-pressed={message.rating === 'up'} disabled={!!voting || disabled} onClick={() => { void vote(message.message_id, 'up').catch(() => {}); }}>Positive</button>
              <button className="button secondary small" aria-label={`Negative feedback for response ${message.message_id}`} aria-pressed={message.rating === 'down'} disabled={!!voting || disabled} onClick={() => setNegativeId(message.message_id)}>Negative</button>
              {message.feedback_comment && <p className="section-note">Your feedback: {message.feedback_comment}</p>}<small role="status">{voting === message.message_id ? 'Saving…' : message.rating ? `Saved: ${message.rating === 'up' ? 'Positive' : 'Negative'}` : 'Not rated'}</small></div>
          </>}
        </article>) : <p>No saved messages in this conversation.</p>}
      </> : !detailError && <EmptyState title="Select a conversation">Open one of your conversations to read its saved transcript.</EmptyState>}
    </section></div>
  </>;
}
