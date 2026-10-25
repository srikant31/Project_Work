import { useEffect, useState } from 'react';
import type { FormEvent } from 'react';
import { ArrowRight, ArrowUpRight, CheckCheck, Clock3, MessageSquare, RefreshCw, ThumbsDown, ThumbsUp } from 'lucide-react';
import { api, errorMessage } from '../api';
import type { ChatResponse, RagConfig, RagPreview } from '../types';
import { ErrorBanner, Spinner } from './Shared';

export default function Assistant({ onOpenRetrieval }: { onOpenRetrieval: () => void }) {
  const [config, setConfig] = useState<RagConfig | null>(null);
  const [configLoading, setConfigLoading] = useState(true);
  const [refresh, setRefresh] = useState(0);
  const [question, setQuestion] = useState('');
  const [topK, setTopK] = useState(3);
  const [answer, setAnswer] = useState<ChatResponse | null>(null);
  const [answeredQuestion, setAnsweredQuestion] = useState('');
  const [conversationId, setConversationId] = useState<string | undefined>();
  const [busy, setBusy] = useState(false);
  const [previewBusy, setPreviewBusy] = useState(false);
  const [preview, setPreview] = useState<RagPreview | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [rating, setRating] = useState<'up' | 'down' | null>(null);
  const [ratingBusy, setRatingBusy] = useState(false);
  const [ratingError, setRatingError] = useState<string | null>(null);

  useEffect(() => {
    let active = true; setConfigLoading(true); setError(null);
    api.ragConfig().then(data => { if (active) setConfig(data); }).catch(caught => { if (active) setError(errorMessage(caught)); }).finally(() => { if (active) setConfigLoading(false); });
    return () => { active = false; };
  }, [refresh]);
  async function submit(event: FormEvent) {
    event.preventDefault(); if (!question.trim() || !config?.configured) return;
    setBusy(true); setError(null); setAnswer(null); setRating(null); setRatingError(null);
    try { const submittedQuestion = question.trim(); const result = await api.chat(submittedQuestion, topK, conversationId); setAnswer(result); setAnsweredQuestion(submittedQuestion); setConversationId(result.conversation_id); }
    catch (caught) { setError(errorMessage(caught)); }
    finally { setBusy(false); }
  }
  async function feedback(value: 'up' | 'down') {
    if (!answer) return; setRatingBusy(true); setRatingError(null);
    try { setRating((await api.feedback(answer.response_id, value)).rating); }
    catch (caught) { setRatingError(errorMessage(caught)); }
    finally { setRatingBusy(false); }
  }
  async function previewContext() {
    if (!question.trim()) return; setPreviewBusy(true); setError(null); setPreview(null);
    try { setPreview(await api.ragPreview(question.trim(), topK)); }
    catch (caught) { setError(errorMessage(caught)); }
    finally { setPreviewBusy(false); }
  }
  function restart() { setConversationId(undefined); setAnswer(null); setAnsweredQuestion(''); setPreview(null); setQuestion(''); setRating(null); setError(null); setRatingError(null); }
  return <>
    <div className="page-heading"><div><span className="eyebrow">WORKSPACE / ASSISTANT</span><h1>Helpdesk assistant<span className="heading-dot">.</span></h1><p>Ask a question. Review the source evidence behind the response.</p></div><span className="outline-tag"><CheckCheck size={15} />Source-verified quotations</span></div>
    <ErrorBanner message={error} />
    {configLoading ? <div className="generation-state"><Spinner label="Checking answer-generation setup…" /></div> : !config?.configured && <section className="generation-state" aria-label="Generation availability"><MessageSquare size={22} /><div><h2>{config ? 'Answer generation is not configured' : 'Generation setup could not be checked'}</h2><p>{config ? 'A language-model provider has not been connected. Inspect the retrieved context below without calling a provider, or search your documents in Retrieval lab.' : 'The server did not provide a generation configuration. Retry the setup check or use source retrieval.'}</p><div className="generation-actions"><button className="button secondary small" onClick={onOpenRetrieval}>Open Retrieval lab<ArrowRight size={14} /></button><button className="text-button" onClick={() => setRefresh(value => value + 1)}><RefreshCw size={13} />Check setup again</button></div></div></section>}
    <section className="panel assistant-form"><div className="retrieval-intro"><span className="accent-icon"><MessageSquare size={21} /></span><div><h2>A question for your sources</h2><p>Responses use checked quotations from eligible online documents.</p></div></div><form onSubmit={submit}><label htmlFor="assistant-question" className="sr-only">Question for the assistant</label><textarea id="assistant-question" value={question} onChange={event => setQuestion(event.target.value)} rows={3} maxLength={3000} placeholder="Ask a question to preview its source evidence and prompt context…" required disabled={busy || previewBusy} /><div className="retrieval-form-footer"><label>Evidence passages<select value={topK} onChange={event => setTopK(Number(event.target.value))} disabled={busy || previewBusy}>{[3, 5, 10].map(value => <option key={value} value={value}>Top {value}</option>)}</select></label><div className="assistant-question-actions"><button className="button secondary" type="button" disabled={busy || previewBusy || !question.trim()} onClick={previewContext}>{previewBusy ? <Spinner label="Building context…" /> : 'Preview context'}</button><button className="button primary" type="submit" disabled={busy || previewBusy || !question.trim() || !config?.configured}>{busy ? <Spinner label="Checking source evidence…" /> : <>Ask assistant<ArrowRight size={17} /></>}</button></div></div></form>{config && <div className="assistant-config"><span>Provider: <strong>{config.provider || 'Not configured'}</strong></span><span>Model: <strong>{config.model || 'Not selected'}</strong></span>{config.context_byte_budget !== undefined && <span>Context limit: <strong>{config.context_byte_budget.toLocaleString()} UTF-8 bytes</strong></span>}</div>}</section>
    {preview && <section className="panel context-preview"><div className="panel-heading"><div><h2>Retrieved context preview</h2><p>For “{preview.question}” · No provider call was made.</p></div><span className="outline-tag">{preview.sources.length} sources</span></div><div className="context-summary"><span><strong>{preview.context_bytes.toLocaleString()}</strong> UTF-8 context bytes</span><span>{preview.prompt_version}</span><span>{Array.isArray(preview.dropped_sources) ? preview.dropped_sources.length : preview.dropped_sources} sources omitted by the context budget</span></div><details className="context-inspector" open><summary>Inspect exact prompt messages and source context</summary><div className="prompt-messages">{preview.messages.map((message, index) => <article key={`${message.role}-${index}`}><h3>{message.role} message</h3><pre>{message.content}</pre></article>)}</div>{preview.sources.length > 0 ? <div className="context-sources"><h3>Source references</h3>{preview.sources.map(source => <article key={source.source_id}><div><strong>{source.source_id} · {source.doc_name}</strong><a className="source-link" href={`/api/documents/${encodeURIComponent(source.doc_id)}/file`} target="_blank" rel="noreferrer">Open source<ArrowUpRight size={13} /></a></div><p>Version {source.version} · {source.page_number !== null ? `Page ${source.page_number}` : source.section_heading || 'Document text'}{source.start_char !== null && source.end_char !== null && ` · Characters ${source.start_char}–${source.end_char}`}</p><blockquote>{source.text}</blockquote></article>)}</div> : <p className="section-note">No eligible source evidence was retrieved for this question.</p>}</details></section>}
    {answer && <section className="assistant-answer"><div className="results-heading"><div><h2>{answer.status === 'insufficient_evidence' ? 'More source evidence is needed' : 'Response from your sources'}</h2><p>For “{answeredQuestion}” · {answer.status === 'insufficient_evidence' ? 'The available evidence did not support a grounded response.' : 'Review the quotations and their source references below.'}</p></div><span className="timing"><Clock3 size={14} />{Math.round(answer.elapsed_ms)} ms</span></div><article className={`panel answer-body ${answer.status === 'insufficient_evidence' ? 'refusal' : ''}`}><p className="passage">{answer.answer}</p><div className="answer-meta"><span>{answer.model} · {answer.prompt_version}</span>{answer.context_bytes !== undefined ? <span>{answer.context_bytes.toLocaleString()} UTF-8 context bytes</span> : answer.context_tokens !== null && <span>{answer.context_tokens} context tokens</span>}</div></article>{answer.citations.map(citation => <article className="panel citation-card" key={`${citation.source_id}-${citation.chunk_id}-${citation.start_char}-${citation.end_char}`}><div className="evidence-card-top"><span className="citation-id">{citation.source_id}</span><div className="evidence-source"><strong>{citation.doc_name}</strong><span>Version {citation.version} · {citation.page_number !== null ? `Page ${citation.page_number}` : citation.section_heading || 'Document text'}</span></div><a className="source-link" href={`/api/documents/${encodeURIComponent(citation.doc_id)}/file`} target="_blank" rel="noreferrer">Open source<ArrowUpRight size={14} /></a></div><blockquote>{citation.quote}</blockquote><span className="mono muted">{citation.start_char !== null && citation.end_char !== null ? `Source characters ${citation.start_char}–${citation.end_char}` : 'Source offsets unavailable'}</span></article>)}<div className="response-feedback"><span>Was this response helpful?</span><button className={`feedback-button ${rating === 'up' ? 'selected' : ''}`} aria-label="Helpful response" aria-pressed={rating === 'up'} disabled={ratingBusy} onClick={() => feedback('up')}><ThumbsUp size={16} /></button><button className={`feedback-button ${rating === 'down' ? 'selected' : ''}`} aria-label="Unhelpful response" aria-pressed={rating === 'down'} disabled={ratingBusy} onClick={() => feedback('down')}><ThumbsDown size={16} /></button>{ratingBusy ? <Spinner label="Saving…" /> : rating && <small role="status">Feedback saved</small>}<button className="text-button new-conversation" onClick={restart} disabled={busy}>Start a new question</button></div><ErrorBanner message={ratingError} /><p className="section-note">Ratings are user feedback. Formal evaluation uses the independently reviewed gold dataset.</p></section>}
  </>;
}
