import { useState } from 'react';
import type { FormEvent } from 'react';
import { ArrowRight, ArrowUpRight, Clock3, FileText, Search, Sparkles } from 'lucide-react';
import { api, errorMessage } from '../api';
import type { SearchResult } from '../types';
import { EmptyState, ErrorBanner, Spinner } from './Shared';

export default function RetrievalLab() {
  const [question, setQuestion] = useState('');
  const [topK, setTopK] = useState(3);
  const [result, setResult] = useState<SearchResult | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  async function submit(event: FormEvent) {
    event.preventDefault(); if (!question.trim()) return; setBusy(true); setError(null); setResult(null);
    try { setResult(await api.search(question.trim(), topK)); } catch (caught) { setError(errorMessage(caught)); } finally { setBusy(false); }
  }
  return <><div className="page-heading"><div><span className="eyebrow">WORKSPACE / RETRIEVAL</span><h1>Retrieval lab<span className="heading-dot">.</span></h1><p>Find the passages behind your next answer.</p></div><span className="outline-tag"><Search size={14} />Evidence search</span></div>
    <section className="panel retrieval-form"><div className="retrieval-intro"><span className="accent-icon"><Sparkles size={21} /></span><div><h2>Ask your knowledge base</h2><p>Search across online sources using meaning, even when the wording is different.</p></div></div><form onSubmit={submit}><label className="sr-only" htmlFor="question">Your question</label><textarea id="question" placeholder="What would you like to find in your documents?" rows={3} value={question} onChange={event => setQuestion(event.target.value)} maxLength={3000} required disabled={busy} /><div className="retrieval-form-footer"><label>Evidence passages<select value={topK} onChange={event => setTopK(Number(event.target.value))} disabled={busy}>{[3, 5, 10].map(count => <option key={count} value={count}>Top {count}</option>)}</select></label><button className="button primary" type="submit" disabled={busy || !question.trim()}>{busy ? <Spinner label="Searching sources…" /> : <>Search knowledge<ArrowRight size={17} /></>}</button></div></form><div className="retrieval-scope"><span className="tiny-dot" />Only online, published documents are searched. Results are source evidence; answer generation is not enabled.</div></section>
    <ErrorBanner message={error} />
    {result ? <section className="results-section"><div className="results-heading"><div><h2>{result.hits.length} {result.hits.length === 1 ? 'passage' : 'passages'} found</h2><p>For “{result.question}”</p></div><span className="timing"><Clock3 size={14} />{Math.round(result.elapsed_ms)} ms</span></div>{!result.hits.length ? <div className="panel"><EmptyState title="No eligible evidence found">Try a different question, or ask an administrator to add an online source.</EmptyState></div> : result.hits.map(hit => <article className="panel evidence-card" key={hit.chunk_id}><div className="evidence-card-top"><span className="evidence-rank">{String(hit.rank).padStart(2, '0')}</span><div className="evidence-source"><strong><FileText size={15} />{hit.doc_name}</strong><span>Version {hit.version} · {hit.page_number !== null ? `Page ${hit.page_number}` : hit.section_heading || 'Document text'}</span></div><span className="similarity">Similarity <strong>{hit.similarity_score.toFixed(3)}</strong></span></div><p className="passage">{hit.text}</p><div className="evidence-bottom"><span className="mono">{hit.start_char !== null && hit.end_char !== null ? `Characters ${hit.start_char}–${hit.end_char}` : 'Legacy source: offsets unavailable'}</span><a href={`/api/documents/${encodeURIComponent(hit.doc_id)}/file`} target="_blank" rel="noreferrer">Open source<ArrowUpRight size={14} /></a></div></article>)}{!!result.hits.length && <p className="section-note">Similarity measures semantic closeness to your question. Review the evidence before using it as an answer.</p>}</section> : !busy && <div className="retrieval-start"><span className="empty-icon"><Search size={27} /></span><h3>Your sources have the details.</h3><p>Enter a question to inspect matching passages and their source references.</p></div>}
  </>;
}
