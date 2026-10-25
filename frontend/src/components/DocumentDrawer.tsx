import { useEffect, useRef, useState } from 'react';
import type { ChangeEvent, FormEvent, KeyboardEvent as ReactKeyboardEvent } from 'react';
import { ArrowDownToLine, FileText, RefreshCw, Replace, X } from 'lucide-react';
import { api, errorMessage } from '../api';
import type { DocumentDetail, UploadResult } from '../types';
import { uploadFileError } from '../validation';
import { ErrorBanner, formatDate, Spinner, StatusBadge } from './Shared';

export default function DocumentDrawer({ id, onClose, onChanged }: { id: string; onClose: () => void; onChanged: (id: string, message: string) => void }) {
  const [data, setData] = useState<DocumentDetail | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [tab, setTab] = useState<'chunks' | 'versions' | 'pipeline'>('chunks');
  const [size, setSize] = useState(200);
  const [overlap, setOverlap] = useState(40);
  const [refresh, setRefresh] = useState(0);
  const closeRef = useRef<HTMLButtonElement>(null);
  const dialogRef = useRef<HTMLElement>(null);
  const replaceRef = useRef<HTMLInputElement>(null);
  const onCloseRef = useRef(onClose); onCloseRef.current = onClose;
  const canRebuild = !!data && ['active', 'failed'].includes(data.document.status);
  useEffect(() => {
    const before = document.activeElement as HTMLElement | null;
    const overflow = document.body.style.overflow;
    document.body.style.overflow = 'hidden'; closeRef.current?.focus();
    const escape = (event: KeyboardEvent) => { if (event.key === 'Escape') onCloseRef.current(); };
    const containFocus = (event: FocusEvent) => { if (dialogRef.current && !dialogRef.current.contains(event.target as Node)) closeRef.current?.focus(); };
    window.addEventListener('keydown', escape);
    document.addEventListener('focusin', containFocus);
    return () => { document.body.style.overflow = overflow; window.removeEventListener('keydown', escape); document.removeEventListener('focusin', containFocus); before?.focus(); };
  }, []);
  useEffect(() => {
    let active = true; setLoading(true); setError(null); setData(null);
    api.document(id).then(result => { if (active) { setData(result); setSize(typeof result.pipeline?.chunk_size === 'number' ? result.pipeline.chunk_size : 200); setOverlap(typeof result.pipeline?.overlap === 'number' ? result.pipeline.overlap : 40); } }).catch(caught => { if (active) setError(errorMessage(caught)); }).finally(() => { if (active) setLoading(false); });
    return () => { active = false; };
  }, [id, refresh]);
  function done(result: UploadResult, action: string) { onChanged(result.document.doc_id, `${action}: ${result.document.doc_name} · version ${result.document.version} · ${result.document.chunk_count} chunks.${result.skipped ? ' Existing index verified.' : ''}${result.cleanup_pending ? ' Previous-version cleanup is pending.' : ''}`); setRefresh(value => value + 1); }
  async function replace(event: ChangeEvent<HTMLInputElement>) {
    const file = event.target.files?.[0]; event.target.value = ''; if (!file || !canRebuild) return;
    const problem = uploadFileError(file); if (problem) { setError(problem); return; }
    setBusy('Replacing document…'); setError(null); const body = new FormData(); body.append('file', file);
    try { done(await api.replace(id, body), 'Replacement processed'); } catch (caught) { setError(errorMessage(caught)); } finally { setBusy(null); }
  }
  async function reindex(event: FormEvent) {
    event.preventDefault(); if (!canRebuild) return; setBusy('Rebuilding index…'); setError(null);
    try { done(await api.reindex(id, size, overlap), 'Index processed'); } catch (caught) { setError(errorMessage(caught)); } finally { setBusy(null); }
  }
  async function toggle() {
    if (!data) return; setBusy('Updating availability…'); setError(null);
    try { const result = await api.availability(id, !data.document.is_online); onChanged(id, `${result.document.doc_name} is now ${result.document.is_online ? 'online' : 'offline'}.`); setRefresh(value => value + 1); } catch (caught) { setError(errorMessage(caught)); } finally { setBusy(null); }
  }
  function trapFocus(event: ReactKeyboardEvent<HTMLElement>) {
    if (event.key !== 'Tab') return;
    const nodes = event.currentTarget.querySelectorAll<HTMLElement>('button:not(:disabled):not([tabindex="-1"]), a[href], input:not(:disabled):not([tabindex="-1"]), select:not(:disabled), summary');
    const first = nodes[0], last = nodes[nodes.length - 1];
    if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last?.focus(); }
    else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first?.focus(); }
  }
  function navigateTabs(event: ReactKeyboardEvent<HTMLButtonElement>) {
    const tabs = ['chunks', 'versions', 'pipeline'] as const;
    let index = tabs.indexOf(tab);
    if (event.key === 'ArrowRight') index = (index + 1) % tabs.length;
    else if (event.key === 'ArrowLeft') index = (index + tabs.length - 1) % tabs.length;
    else if (event.key === 'Home') index = 0;
    else if (event.key === 'End') index = tabs.length - 1;
    else return;
    event.preventDefault(); setTab(tabs[index]); document.getElementById(`source-tab-${tabs[index]}`)?.focus();
  }
  return <div className="drawer-backdrop" onMouseDown={event => { if (event.target === event.currentTarget) onClose(); }}><aside ref={dialogRef} className="document-drawer" role="dialog" aria-modal="true" aria-label="Document source inspector" aria-labelledby={data ? 'document-title' : undefined} onKeyDown={trapFocus}>
    <div className="drawer-top"><span className="eyebrow">SOURCE INSPECTOR</span><button ref={closeRef} className="icon-button" aria-label="Close document inspector" onClick={onClose}><X size={21} /></button></div>
    <ErrorBanner message={error} />
    {loading ? <div className="loading-box"><Spinner label="Loading source evidence…" /></div> : data && <>
      <div className="drawer-heading"><span className={`file-icon large ${data.document.doc_type}`}><FileText size={27} /></span><h2 id="document-title">{data.document.doc_name}</h2><div className="drawer-tags"><StatusBadge document={data.document} /><span>{data.document.doc_type.toUpperCase()}</span><span>Version {data.document.version}</span><span>{data.document.category_name}</span></div></div>
      <div className="drawer-actions"><a className="button secondary small" href={`/api/documents/${encodeURIComponent(id)}/file`} target="_blank" rel="noreferrer"><ArrowDownToLine size={15} />Original file</a><button className="button secondary small" disabled={Boolean(busy) || !canRebuild} onClick={() => replaceRef.current?.click()}><Replace size={15} />Replace file</button><input ref={replaceRef} className="sr-only" tabIndex={-1} type="file" accept=".txt,.md,.pdf,.docx" aria-label="Choose replacement file" disabled={Boolean(busy) || !canRebuild} onChange={replace} /><button className="button secondary small" onClick={toggle} disabled={Boolean(busy) || data.document.status !== 'active'}>Make {data.document.is_online ? 'offline' : 'online'}</button></div>
      {!canRebuild && <p className="section-note">This version is available for inspection. Open the current active or failed version to replace the file or rebuild its index.</p>}
      {busy && <div className="processing-strip" role="status"><Spinner label={busy} />{busy !== 'Updating availability…' && <span>The new version is published only after its processing succeeds.</span>}</div>}
      {data.document.last_error && <ErrorBanner message={`Last ingestion: ${data.document.last_error}`} />}
      {!!data.warnings.length && <div className="source-warnings"><strong>Extraction notes</strong>{data.warnings.map((warning, index) => <p key={index}>{warning}</p>)}</div>}
      <div className="drawer-stats"><div><strong>{data.chunks.length}</strong><span>Stored chunks</span></div><div><strong>{data.source_characters.toLocaleString()}</strong><span>Source characters</span></div><div><strong>{data.document.last_job_status || 'Unknown'}</strong><span>Last ingestion job</span></div></div>
      <div className="tabs" role="tablist" aria-label="Document details">{(['chunks', 'versions', 'pipeline'] as const).map(value => <button key={value} id={`source-tab-${value}`} role="tab" aria-controls="source-tab-panel" tabIndex={tab === value ? 0 : -1} aria-selected={tab === value} onKeyDown={navigateTabs} onClick={() => setTab(value)} className={tab === value ? 'active' : ''}>{value === 'chunks' ? 'Chunks & evidence' : value === 'versions' ? 'Version history' : 'Pipeline'}</button>)}</div>
      <div id="source-tab-panel" role="tabpanel" aria-labelledby={`source-tab-${tab}`} className="drawer-tab-content">
        {tab === 'chunks' && <><p className="section-note">Exact passages from the normalized source. Offsets identify the supporting text for evaluation.</p>{!data.chunks.length ? <p className="subtle">No stored chunks for this version.</p> : data.chunks.map(chunk => <article className="chunk-card" key={chunk.chunk_id}><div className="chunk-meta"><strong>Chunk {chunk.index + 1}</strong><span>{chunk.token_count ?? 'Unknown'} tokens</span></div><div className="evidence-location">{chunk.page_number !== null ? `Page ${chunk.page_number}` : chunk.section_heading || 'Document text'}{chunk.start_char !== null && chunk.end_char !== null && <span> · Characters {chunk.start_char}–{chunk.end_char}</span>}</div><p className="passage">{chunk.text}</p><span className="mono muted">{chunk.chunk_id}</span></article>)}</>}
        {tab === 'versions' && <div className="version-list">{data.versions.map(version => <article key={version.doc_id}><span className="version-number">v{version.version}</span><div><strong>{version.doc_name}</strong><p>{formatDate(version.created_at)} · {version.chunk_count} chunks</p></div><StatusBadge document={version} /></article>)}</div>}
        {tab === 'pipeline' && <><dl className="source-details"><dt>Source identity</dt><dd>{data.document.source_key || 'Not captured for this legacy source'}</dd><dt>SHA-256</dt><dd className="mono">{data.document.source_hash || 'Not captured for this legacy source'}</dd></dl><details className="config-details"><summary>Stored pipeline configuration</summary><pre>{data.pipeline ? JSON.stringify(data.pipeline, null, 2) : 'No recorded configuration for this legacy source.'}</pre></details><form className="reindex-form" onSubmit={reindex}><h3>Rebuild this source’s index</h3><p>Rebuilding creates a new version and preserves this source’s online or offline availability.</p><div className="form-grid"><label>Content tokens per chunk<input type="number" min={8} max={4096} step={1} required value={size} onChange={event => setSize(Number(event.target.value))} disabled={Boolean(busy) || !canRebuild} /></label><label>Overlap tokens<input type="number" min={0} max={Math.max(0, size - 1)} step={1} required value={overlap} onChange={event => setOverlap(Number(event.target.value))} disabled={Boolean(busy) || !canRebuild} /></label></div><button className="button secondary" type="submit" disabled={Boolean(busy) || !canRebuild || !Number.isInteger(size) || !Number.isInteger(overlap) || size < 8 || size > 4096 || overlap < 0 || overlap >= size}><RefreshCw size={15} />Rebuild index</button></form></>}
      </div>
    </>}
  </aside></div>;
}
