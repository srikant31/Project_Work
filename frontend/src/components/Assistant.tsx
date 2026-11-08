import { useEffect, useState } from 'react';
import type { FormEvent } from 'react';
import { ArrowRight, CheckCheck, MessageSquare, RefreshCw } from 'lucide-react';
import { api, errorMessage } from '../api';
import type { ChatResponse, RagConfig, RagPreview } from '../types';
import ConversationHistory from './ConversationHistory';
import { ErrorBanner, Spinner } from './Shared';

export default function Assistant({ onOpenRetrieval, initialConversationId, onOpenHistory }: { onOpenRetrieval: () => void; initialConversationId?: string; onOpenHistory: (id?: string) => void }) {
  const [config, setConfig] = useState<RagConfig | null>(null);
  const [configLoading, setConfigLoading] = useState(true);
  const [refresh, setRefresh] = useState(0);
  const [question, setQuestion] = useState('');
  const [topK, setTopK] = useState(3);
  const [answer, setAnswer] = useState<ChatResponse | null>(null);
  const [conversationId, setConversationId] = useState<string | undefined>(initialConversationId);
  const [busy, setBusy] = useState(false);
  const [previewBusy, setPreviewBusy] = useState(false);
  const [preview, setPreview] = useState<RagPreview | null>(null);
  const [error, setError] = useState<string | null>(null);


  useEffect(() => {
    let active = true; setConfigLoading(true); setError(null);
    api.ragConfig().then(data => { if (active) setConfig(data); }).catch(caught => { if (active) setError(errorMessage(caught)); }).finally(() => { if (active) setConfigLoading(false); });
    return () => { active = false; };
  }, [refresh]);
  async function submit(event: FormEvent) {
    event.preventDefault(); if (!question.trim() || !config?.configured) return;
    setBusy(true); setError(null);
    try { const submittedQuestion = question.trim(); const result = await api.chat(submittedQuestion, topK, conversationId); setAnswer(result); setQuestion(''); setPreview(null); setConversationId(result.conversation_id); }
    catch (caught) { setError(errorMessage(caught)); }
    finally { setBusy(false); }
  }
  async function previewContext() {
    if (!question.trim()) return; setPreviewBusy(true); setError(null); setPreview(null);
    try { setPreview(await api.ragPreview(question.trim(), topK)); }
    catch (caught) { setError(errorMessage(caught)); }
    finally { setPreviewBusy(false); }
  }
  function restart() { setConversationId(undefined); setAnswer(null);  setPreview(null); setQuestion('');  setError(null);  }
  return <>
    <div className="page-heading"><div><span className="eyebrow">WORKSPACE / ASSISTANT</span><h1>Helpdesk assistant<span className="heading-dot">.</span></h1><p>Ask a question. Review the source evidence behind the response.</p></div><span className="outline-tag"><CheckCheck size={15} />Source-verified quotations</span></div>
    <ErrorBanner message={error} />
    {conversationId && <div className="history-actions"><p>Continuing a saved conversation. Follow-ups use recent messages from this chat only, with fresh source retrieval. Context preview searches the typed question alone; Ask assistant resolves chat references.</p><button className="button secondary" disabled={busy} onClick={() => onOpenHistory(conversationId)}>View saved transcript</button><button className="button secondary" disabled={busy} onClick={restart}>New conversation</button></div>}
    {configLoading ? <div className="generation-state"><Spinner label="Checking answer-generation setup…" /></div> : !config?.configured && <section className="generation-state" aria-label="Generation availability"><MessageSquare size={22} /><div><h2>{config ? 'Answer generation is not configured' : 'Generation setup could not be checked'}</h2><p>{config ? 'A language-model provider has not been connected. Inspect the retrieved context below without calling a provider, or search your documents in Knowledge Search.' : 'The server did not provide a generation configuration. Retry the setup check or use source retrieval.'}</p><div className="generation-actions"><button className="button secondary small" onClick={onOpenRetrieval}>Open Knowledge Search<ArrowRight size={14} /></button><button className="text-button" onClick={() => setRefresh(value => value + 1)}><RefreshCw size={13} />Check setup again</button></div></div></section>}
    {answer?.status === 'clarification_required' && <p role="status">Please clarify your question</p>}
    {conversationId && <ConversationHistory selectedId={conversationId} onSelect={() => {}} onNew={restart} onContinue={() => {}} transcriptOnly revision={answer?.response_id} disabled={busy} />}
    <section className="panel assistant-form"><div className="retrieval-intro"><span className="accent-icon"><MessageSquare size={21} /></span><div><h2>A question for your sources</h2><p>Responses use checked quotations from eligible online documents.</p></div></div><form onSubmit={submit}><label htmlFor="assistant-question" className="sr-only">Question for the assistant</label><textarea id="assistant-question" value={question} onChange={event => setQuestion(event.target.value)} rows={3} maxLength={3000} placeholder="Ask a question to preview its source evidence and prompt context…" required disabled={busy || previewBusy} /><div className="retrieval-form-footer"><label>Evidence passages<select value={topK} onChange={event => setTopK(Number(event.target.value))} disabled={busy || previewBusy}>{[3, 5, 10].map(value => <option key={value} value={value}>Top {value}</option>)}</select></label><div className="assistant-question-actions"><button className="button secondary" type="button" disabled={busy || previewBusy || !question.trim()} onClick={previewContext}>{previewBusy ? <Spinner label="Building context…" /> : 'Preview context'}</button><button className="button primary" type="submit" disabled={busy || previewBusy || !question.trim() || !config?.configured}>{busy ? <Spinner label="Checking source evidence…" /> : <>Ask assistant<ArrowRight size={17} /></>}</button></div></div></form>{config && <div className="assistant-config"><span>Retrieval: <strong>{config.retrieval_mode || 'vector'}</strong></span><span>Provider: <strong>{config.provider || 'Not configured'}</strong></span><span>Model: <strong>{config.model || 'Not selected'}</strong></span>{config.context_byte_budget !== undefined && <span>Context limit: <strong>{config.context_byte_budget.toLocaleString()} UTF-8 bytes</strong></span>}</div>}</section>
    {preview && <section className="panel context-preview"><div className="panel-heading"><div><h2>Retrieved context preview</h2><p>For “{preview.question}” · No provider call was made.</p></div><span className="outline-tag">{preview.sources.length} sources</span></div><div className="context-summary"><span><strong>{preview.context_bytes.toLocaleString()}</strong> UTF-8 context bytes</span><span>{preview.prompt_version}</span><span>{Array.isArray(preview.dropped_sources) ? preview.dropped_sources.length : preview.dropped_sources} sources omitted by the context budget</span></div><details className="context-inspector" open><summary>Inspect exact prompt messages and source context</summary><div className="prompt-messages">{preview.messages.map((message, index) => <article key={`${message.role}-${index}`}><h3>{message.role} message</h3><pre>{message.content}</pre></article>)}</div>{preview.sources.length > 0 ? <div className="context-sources"><h3>Source references</h3>{preview.sources.map(source => <article key={source.source_id}><div><strong>{source.source_id} · {source.doc_name}</strong><span className="section-note">Exact retrieved chunk below</span></div><p>Version {source.version} · {source.page_number !== null ? `Page ${source.page_number}` : source.section_heading || 'Document text'}{source.start_char !== null && source.end_char !== null && ` · Characters ${source.start_char}–${source.end_char}`}</p><blockquote>{source.text}</blockquote></article>)}</div> : <p className="section-note">No eligible source evidence was retrieved for this question.</p>}</details></section>}

  </>;
}
