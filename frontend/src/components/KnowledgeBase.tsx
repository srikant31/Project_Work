import { useEffect, useRef, useState } from 'react';
import type { ChangeEvent, FormEvent } from 'react';
import { ArrowDownToLine, ArrowUpRight, FileText, Filter, Layers3, Plus, RefreshCw, Search, UploadCloud, X } from 'lucide-react';
import { api, errorMessage } from '../api';
import type { Category, DocumentFilters, KnowledgeDocument, Summary } from '../types';
import { dateRangeError, formatUtc, versionError } from '../adminData';
import { uploadFileError } from '../validation';
import DocumentDrawer from './DocumentDrawer';
import { EmptyState, ErrorBanner, Notice, Spinner, StatusBadge } from './Shared';

export default function KnowledgeBase() {
  const [documents, setDocuments] = useState<KnowledgeDocument[]>([]);
  const [summary, setSummary] = useState<Summary | null>(null);
  const [categories, setCategories] = useState<Category[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [query, setQuery] = useState('');
  const [categoryFilter, setCategoryFilter] = useState('');
  const [typeFilter, setTypeFilter] = useState('');
  const [statusFilter, setStatusFilter] = useState('');
  const [uploadFromDraft, setUploadFromDraft] = useState('');
  const [uploadToDraft, setUploadToDraft] = useState('');
  const [versionDraft, setVersionDraft] = useState('');
  const [storedFilters, setStoredFilters] = useState<DocumentFilters>({});
  const [scope, setScope] = useState<'latest_sources' | 'matching_versions'>('latest_sources');
  const [filterError, setFilterError] = useState<string | null>(null);
  const [selected, setSelected] = useState<string | null>(null);
  const [refresh, setRefresh] = useState(0);
  const [showUpload, setShowUpload] = useState(false);
  const [pendingId, setPendingId] = useState<string | null>(null);
  const uploadAnchor = useRef<HTMLDivElement>(null);

  useEffect(() => {
    let active = true; setLoading(true); setError(null); setDocuments([]); setSummary(null);
    Promise.all([api.documents(storedFilters), api.categories()]).then(([data, taxonomy]) => {
      if (active) { setDocuments(data.documents); setSummary(data.summary); setCategories(taxonomy.categories); setScope(data.scope || (Object.keys(storedFilters).length ? 'matching_versions' : 'latest_sources')); }
    }).catch(caught => { if (active) setError(errorMessage(caught)); }).finally(() => { if (active) setLoading(false); });
    return () => { active = false; };
  }, [refresh, storedFilters]);

  function applyStoredFilters(event: FormEvent) {
    event.preventDefault(); const problem = dateRangeError(uploadFromDraft, uploadToDraft) || versionError(versionDraft); setFilterError(problem);
    if (!problem) setStoredFilters({ ...(uploadFromDraft ? { uploaded_from: uploadFromDraft } : {}), ...(uploadToDraft ? { uploaded_to: uploadToDraft } : {}), ...(versionDraft ? { version: Number(versionDraft) } : {}) });
  }

  function changed(id: string, message: string) { setSelected(id); setNotice(message); setRefresh(value => value + 1); }
  async function toggle(document: KnowledgeDocument) {
    setPendingId(document.doc_id); setError(null);
    try { const result = await api.availability(document.doc_id, !document.is_online); setNotice(`${result.document.doc_name} is now ${result.document.is_online ? 'online and available to retrieval' : 'offline and excluded from retrieval'}.`); setRefresh(value => value + 1); }
    catch (caught) { setError(errorMessage(caught)); }
    finally { setPendingId(null); }
  }
  const filtered = documents.filter(document => (!categoryFilter || document.category_id === categoryFilter)
    && (!typeFilter || document.doc_type === typeFilter)
    && (!statusFilter || (statusFilter === 'online' ? document.status === 'active' && document.is_online
      : statusFilter === 'offline' ? document.status === 'active' && !document.is_online : document.status === statusFilter))
    && `${document.doc_name} ${document.source_key || ''}`.toLowerCase().includes(query.toLowerCase()));

  return <>
    <div className="page-heading"><div><span className="eyebrow">WORKSPACE / KNOWLEDGE</span><h1>Knowledge base<span className="heading-dot">.</span></h1><p>Give your team a reliable source of truth.</p></div><button className="button primary" onClick={() => { setShowUpload(true); setTimeout(() => uploadAnchor.current?.scrollIntoView({ behavior: 'smooth', block: 'nearest' }), 0); }}><Plus size={17} />Add document</button></div>
    <Notice message={notice} onClose={() => setNotice(null)} /><ErrorBanner message={error} />
    <div className="metrics-grid">{[
      { label: scope === 'matching_versions' ? 'Matching versions' : 'Total documents', value: summary?.documents, icon: FileText, note: scope === 'matching_versions' ? 'Stored versions matching dates/version' : 'Latest source versions' },
      { label: 'Online', value: summary?.online, icon: ArrowUpRight, note: 'Available to retrieval' },
      { label: 'Offline', value: summary?.offline, icon: ArrowDownToLine, note: 'Excluded from retrieval' },
      { label: 'Indexed chunks', value: summary?.chunks, icon: Layers3, note: 'Stored evidence passages' },
    ].map(metric => <div className="metric-card" key={metric.label}><div className="metric-top"><span>{metric.label}</span><metric.icon size={18} /></div><strong>{metric.value === undefined ? '—' : metric.value.toLocaleString()}</strong><small>{metric.note}</small></div>)}</div>
    {showUpload && <div ref={uploadAnchor}><UploadPanel categories={categories} onClose={() => setShowUpload(false)} onDone={(id, message) => { setShowUpload(false); changed(id, message); }} /></div>}
    <section className="panel document-panel"><div className="panel-heading"><div><h2>Your documents <span className="count-pill">{documents.length}</span></h2><p>Manage sources, review chunks and control availability.</p></div><button className="icon-button" title="Refresh documents" aria-label="Refresh documents" disabled={loading} onClick={() => setRefresh(value => value + 1)}><RefreshCw size={17} className={loading ? 'spin' : ''} /></button></div>
      <form className="admin-date-form document-date-form" onSubmit={applyStoredFilters}><label htmlFor="document-uploaded-from">Uploaded from (UTC)<input aria-label="Uploaded from (UTC)" id="document-uploaded-from" type="date" value={uploadFromDraft} onChange={event => setUploadFromDraft(event.target.value)} /></label><label htmlFor="document-uploaded-to">Uploaded through (UTC)<input aria-label="Uploaded through (UTC)" id="document-uploaded-to" type="date" value={uploadToDraft} onChange={event => setUploadToDraft(event.target.value)} /></label><label htmlFor="document-version-filter">Document version<input aria-label="Document version" id="document-version-filter" type="number" min="1" step="1" inputMode="numeric" placeholder="Any version" value={versionDraft} onChange={event => setVersionDraft(event.target.value)} /></label><button className="button secondary small" type="submit" disabled={loading}>Apply dates/version</button><button className="text-button" type="button" disabled={loading} onClick={() => { setUploadFromDraft(''); setUploadToDraft(''); setVersionDraft(''); setFilterError(null); setStoredFilters({}); setStatusFilter(''); }}>Reset dates/version</button></form>
      <div className="admin-panel-padding"><ErrorBanner message={filterError} /><p className="admin-explanation" aria-live="polite">{scope === 'matching_versions' ? 'Showing matching stored versions, including historical versions. Counts describe the returned versions.' : 'Showing the latest version of each source.'} Upload dates include the entire selected UTC days and refer to each version’s creation date. Adding a date or version filter searches version history.</p></div>
      <div className="table-toolbar"><div className="search-input"><Search size={17} /><input aria-label="Search documents" placeholder="Search documents…" value={query} onChange={event => setQuery(event.target.value)} /></div><div className="filter-select"><Filter size={15} /><select aria-label="Filter by category" value={categoryFilter} onChange={event => setCategoryFilter(event.target.value)}><option value="">All categories</option>{categories.map(category => <option value={category.category_id} key={category.category_id}>{category.category_name}</option>)}</select></div><div className="filter-select"><select aria-label="Filter by document type" value={typeFilter} onChange={event => setTypeFilter(event.target.value)}><option value="">All file types</option>{['pdf', 'docx', 'txt', 'md'].map(type => <option value={type} key={type}>{type.toUpperCase()}</option>)}</select></div><div className="filter-select"><select aria-label="Filter by status" value={statusFilter} onChange={event => setStatusFilter(event.target.value)}><option value="">All statuses</option>{['online', 'offline', 'deactivated', 'failed', 'staging'].map(status => <option value={status} key={status}>{status[0].toUpperCase() + status.slice(1)}</option>)}</select></div></div>
      {loading && !documents.length ? <div className="loading-box"><Spinner label="Loading your documents…" /></div> : !filtered.length ? <EmptyState title={documents.length || scope === 'matching_versions' ? 'No matching documents' : 'A fresh start for your knowledge'}>{documents.length || scope === 'matching_versions' ? 'Try another name, category, date, or version.' : 'Add a TXT, Markdown, PDF or Word document to begin.'}</EmptyState> : <div className="table-scroll" role="region" aria-label="Document table. Scroll horizontally to view all columns." tabIndex={0}><table><thead><tr><th>Document</th><th>Category</th><th>Status</th><th>Chunks</th><th>Uploaded (UTC)</th><th><span className="sr-only">Actions</span></th></tr></thead><tbody>{filtered.map(document => <tr key={document.doc_id}><td><button className="document-name" onClick={() => setSelected(document.doc_id)}><span className={`file-icon ${document.doc_type}`}><FileText size={20} /></span><span><strong>{document.doc_name}</strong><small>{document.doc_type.toUpperCase()}<span>·</span>Version {document.version}{!document.source_hash && <><span>·</span>Legacy source</>}</small></span></button></td><td><span className="category-label">{document.category_name || 'Uncategorized'}</span></td><td><button className="status-button" disabled={document.status !== 'active' || pendingId === document.doc_id} onClick={() => toggle(document)} title={document.status === 'active' ? `Make ${document.is_online ? 'offline' : 'online'}` : document.last_error || document.status}><StatusBadge document={document} /></button></td><td className="numeric">{document.chunk_count}</td><td className="date-cell">{formatUtc(document.created_at)}</td><td><button className="icon-button" aria-label={`Inspect ${document.doc_name}`} onClick={() => setSelected(document.doc_id)}><ArrowUpRight size={18} /></button></td></tr>)}</tbody></table></div>}
      <div className="table-footer"><span>{filtered.length} {filtered.length === 1 ? 'document' : 'documents'}{categoryFilter || typeFilter || statusFilter || query ? ' matching your filters' : ' in your workspace'}</span><span><span className="tiny-dot" />Online sources are used in retrieval</span></div>
    </section>
    <div className="workspace-note"><span>BUILT ON YOUR SOURCES</span><p>Every result links back to a document version and its original evidence.</p></div>
    {selected && <DocumentDrawer id={selected} onClose={() => setSelected(null)} onChanged={changed} />}
  </>;
}

function UploadPanel({ categories, onClose, onDone }: { categories: Category[]; onClose: () => void; onDone: (id: string, message: string) => void }) {
  const [file, setFile] = useState<File | null>(null);
  const [category, setCategory] = useState(categories[0]?.category_id || '');
  const [name, setName] = useState('');
  const [online, setOnline] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  function chooseFile(event: ChangeEvent<HTMLInputElement>) {
    const selected = event.target.files?.[0] || null;
    const problem = selected ? uploadFileError(selected) : null;
    setError(problem); setFile(problem ? null : selected);
    if (problem) event.target.value = '';
  }
  async function submit(event: FormEvent) {
    event.preventDefault(); if (!file || !category) return; setBusy(true); setError(null);
    const body = new FormData(); body.append('file', file); body.append('category_id', category); body.append('is_online', String(online)); if (name.trim()) body.append('doc_name', name.trim());
    try { const result = await api.upload(body); onDone(result.document.doc_id, `${result.skipped ? 'Existing version verified' : 'Document processed'}: ${result.document.doc_name} · ${result.document.chunk_count} chunks.${result.cleanup_pending ? ' Previous-version cleanup is pending.' : ''}`); }
    catch (caught) { setError(errorMessage(caught)); setBusy(false); }
  }
  return <section className="panel upload-panel"><div className="panel-heading"><div><h2>Add a document</h2><p>Extract, chunk and index your source in one step.</p></div><button className="icon-button" aria-label="Close upload form" disabled={busy} onClick={onClose}><X size={18} /></button></div><form onSubmit={submit}><ErrorBanner message={error} /><label className={`file-drop ${file ? 'has-file' : ''}`}><UploadCloud size={27} /><strong>{file ? file.name : 'Choose a document'}</strong><span>TXT, Markdown, PDF or DOCX · Up to 20 MB</span><input type="file" accept=".txt,.md,.pdf,.docx" aria-label="Choose document to upload" required disabled={busy} onChange={chooseFile} /></label><div className="form-grid"><label>Document name <span className="optional">(optional)</span><input placeholder="Defaults to the filename" value={name} disabled={busy} onChange={event => setName(event.target.value)} maxLength={255} /></label><label>Category<select value={category} onChange={event => setCategory(event.target.value)} required disabled={busy}><option value="" disabled>Select a category</option>{categories.map(item => <option key={item.category_id} value={item.category_id}>{item.category_name}</option>)}</select></label></div><div className="form-actions"><label className="checkbox-label"><input type="checkbox" checked={online} disabled={busy} onChange={event => setOnline(event.target.checked)} /><span>Make available to retrieval after processing</span></label><button className="button primary" disabled={!file || !category || busy} type="submit">{busy ? <Spinner label="Processing document…" /> : <><Plus size={16} />Add document</>}</button></div>{busy && <p className="processing-note" role="status">Extracting text, creating chunks and storing embeddings. This may take a moment.</p>}</form></section>;
}
