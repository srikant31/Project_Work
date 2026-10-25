import { useEffect, useRef, useState } from 'react';
import type { ChangeEvent } from 'react';
import { ArrowRight, Check, Download, FlaskConical, Plus, RefreshCw, Upload, X } from 'lucide-react';
import { api, errorMessage } from '../api';
import { parseGoldQuestions, remapGoldDocumentIds } from '../experimentData';
import type { ExperimentCandidate, ExperimentConfig, ExperimentReport } from '../types';
import { EmptyState, ErrorBanner, Spinner } from './Shared';

interface ApplyResult { doc_id: string; name: string; status: 'completed' | 'failed'; detail: string }
function rate(value: number | null) { return value === null ? '—' : `${(value * 100).toFixed(1)}%`; }
function decimal(value: number | null) { return value === null ? '—' : value.toFixed(3); }

export default function Experiments() {
  const [config, setConfig] = useState<ExperimentConfig | null>(null);
  const [selected, setSelected] = useState<string[]>([]);
  const [sourceWarning, setSourceWarning] = useState<string | null>(null);
  const [dataset, setDataset] = useState('[]');
  const [datasetWarning, setDatasetWarning] = useState<string | null>(null);
  const [datasetNote, setDatasetNote] = useState<string | null>(null);
  const [candidates, setCandidates] = useState<ExperimentCandidate[]>([]);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [report, setReport] = useState<ExperimentReport | null>(null);
  const [targets, setTargets] = useState<string[]>([]);
  const [applying, setApplying] = useState<number | null>(null);
  const [applyResults, setApplyResults] = useState<ApplyResult[]>([]);
  const [applyProgress, setApplyProgress] = useState<string | null>(null);
  const [refresh, setRefresh] = useState(0);
  const datasetInitialized = useRef(false);
  const selectedRef = useRef(selected); selectedRef.current = selected;
  const fileInput = useRef<HTMLInputElement>(null);
  useEffect(() => {
    let active = true; setLoading(true); setError(null);
    api.experimentConfig().then(data => {
      if (!active) return; setConfig(data);
      const eligible = new Set(data.documents.map(document => document.doc_id));
      const kept = selectedRef.current.filter(id => eligible.has(id));
      if (kept.length !== selectedRef.current.length) {
        setSelected(kept);
        setSourceWarning('Some selected sources changed or became unavailable and were cleared. Review the corpus and gold source labels before running again.');
      }
      if (!datasetInitialized.current) { setDataset(JSON.stringify(data.example, null, 2)); setCandidates(data.presets.map(value => ({ ...value }))); datasetInitialized.current = true; }
    }).catch(caught => { if (active) setError(errorMessage(caught)); }).finally(() => { if (active) setLoading(false); });
    return () => { active = false; };
  }, [refresh]);
  const locked = busy || applying !== null;
  function toggle(id: string) { setSelected(values => values.includes(id) ? values.filter(value => value !== id) : [...values, id]); }
  function update(index: number, field: keyof ExperimentCandidate, value: number) { setCandidates(values => values.map((candidate, current) => current === index ? { ...candidate, [field]: value } : candidate)); }
  async function importDataset(event: ChangeEvent<HTMLInputElement>) {
    const file = event.target.files?.[0]; event.target.value = ''; if (!file) return;
    setError(null);
    try { if (file.size > 2 * 1024 * 1024) throw new Error('Choose a question JSON file smaller than 2 MB.'); const text = await file.text(); parseGoldQuestions(text); setDataset(text); setDatasetWarning(null); setDatasetNote(null); }
    catch (caught) { setError(errorMessage(caught)); }
  }
  async function run() {
    setError(null);
    try {
      if (!selected.length || selected.length > 10) throw new Error('Select between one and ten source documents.');
      const questions = parseGoldQuestions(dataset);
      if (!candidates.length || candidates.length > 12) throw new Error('Use between one and twelve configurations.');
      if (candidates.some(value => !Number.isInteger(value.chunk_size) || !Number.isInteger(value.overlap) || !Number.isInteger(value.top_k) || value.chunk_size < 8 || value.chunk_size > 4096 || value.overlap < 0 || value.overlap >= value.chunk_size || value.top_k < 1 || value.top_k > 20)) throw new Error('Check each configuration: chunk size 8–4096, overlap below chunk size, and top K 1–20. The embedding model may impose a smaller chunk limit.');
      setBusy(true); setReport(null); setApplyResults([]); setSourceWarning(null);
      const result = await api.runExperiment(selected, questions, candidates);
      setReport(result); setTargets(result.corpus.map(document => document.doc_id));
    } catch (caught) { setError(errorMessage(caught)); }
    finally { setBusy(false); }
  }
  async function apply(index: number) {
    if (!report || locked) return;
    const candidate = report.results[index]; const currentTargets = [...targets];
    const names = new Map(report.corpus.map((document, position) => [position, document.doc_name]));
    setApplying(index); setApplyResults([]); setError(null);
    const results: ApplyResult[] = [];
    const replacements = new Map<string, string>();
    for (let position = 0; position < currentTargets.length; position++) {
      const id = currentTargets[position]; const name = names.get(position) || id;
      setApplyProgress(`Reindexing ${position + 1} of ${currentTargets.length}: ${name}`);
      try {
        const result = await api.reindex(id, candidate.chunk_size, candidate.overlap);
        currentTargets[position] = result.document.doc_id;
        replacements.set(id, result.document.doc_id);
        replacements.set(report.corpus[position].doc_id, result.document.doc_id);
        setSelected(values => values.map(value => value === id ? result.document.doc_id : value));
        results.push({ doc_id: id, name, status: 'completed', detail: `Version ${result.document.version} · ${result.document.chunk_count} chunks${result.cleanup_pending ? ' · cleanup pending' : ''}` });
      } catch (caught) { results.push({ doc_id: id, name, status: 'failed', detail: errorMessage(caught) }); }
      setApplyResults([...results]);
    }
    const updated = remapGoldDocumentIds(dataset, replacements);
    setDataset(updated.dataset); setDatasetWarning(updated.warning);
    setDatasetNote(updated.changed ? `${updated.changed} expected document ${updated.changed === 1 ? 'label updated' : 'labels updated'} to the reindexed versions. The completed report remains unchanged.` : null);
    setTargets(currentTargets); setApplying(null); setApplyProgress(null); setRefresh(value => value + 1);
  }
  function download() {
    if (!report) return;
    const url = URL.createObjectURL(new Blob([JSON.stringify(report, null, 2)], { type: 'application/json' }));
    const link = document.createElement('a'); link.href = url; link.download = `retrieval-experiment-${report.run_id}.json`; link.click(); setTimeout(() => URL.revokeObjectURL(url), 1000);
  }
  let questionCount: number | null = null;
  try { const parsed = JSON.parse(dataset); if (Array.isArray(parsed)) questionCount = parsed.length; } catch { /* Editable draft is validated on run. */ }
  return <>
    <div className="page-heading"><div><span className="eyebrow">WORKSPACE / EXPERIMENTS</span><h1>Retrieval experiments<span className="heading-dot">.</span></h1><p>Compare chunking and search settings against your reviewed evidence.</p></div><button className="button secondary" onClick={() => setRefresh(value => value + 1)} disabled={loading || locked}><RefreshCw size={15} className={loading ? 'spin' : ''} />Refresh sources</button></div>
    <ErrorBanner message={error} />
    {loading && !config ? <div className="panel loading-box"><Spinner label="Loading experiment setup…" /></div> : config && <>
      <section className="panel experiment-section"><div className="panel-heading"><div><h2><span className="step-number">1</span>Select the corpus</h2><p>Use up to ten online sources. Each run records the exact document versions and hashes.</p></div><span className="outline-tag">{selected.length} selected</span></div>{sourceWarning && <div className="experiment-source-warning"><ErrorBanner message={sourceWarning} /></div>}{!config.documents.length ? <EmptyState title="No eligible sources yet">Upload and publish documents in Knowledge base before running a comparison.</EmptyState> : <div className="corpus-grid">{config.documents.map(document => <label className={`corpus-option ${selected.includes(document.doc_id) ? 'selected' : ''}`} key={document.doc_id}><input type="checkbox" checked={selected.includes(document.doc_id)} disabled={locked || (selected.length >= 10 && !selected.includes(document.doc_id))} onChange={() => toggle(document.doc_id)} /><span><strong>{document.doc_name}</strong><small>Version {document.version} · {document.source_key}</small><span className="mono">{document.source_hash?.slice(0, 12) || 'Unknown source hash'}</span></span></label>)}</div>}</section>
      <section className="panel experiment-section"><div className="panel-heading"><div><h2><span className="step-number">2</span>Add gold questions</h2><p>Review expected sources and verbatim evidence before treating this dataset as gold.</p></div><button className="button secondary small" onClick={() => fileInput.current?.click()} disabled={locked}><Upload size={14} />Import JSON</button><input ref={fileInput} className="sr-only" tabIndex={-1} aria-label="Import gold question JSON" type="file" accept=".json,application/json" onChange={importDataset} disabled={locked} /></div><div className="experiment-editor"><ErrorBanner message={datasetWarning} />{datasetNote && <p className="section-note" role="status">{datasetNote}</p>}<label className="sr-only" htmlFor="gold-dataset">Gold question JSON array</label><textarea id="gold-dataset" className="json-editor" spellCheck={false} rows={12} value={dataset} onChange={event => { setDataset(event.target.value); setDatasetWarning(null); setDatasetNote(null); }} disabled={locked} /><div className="editor-note"><span>{questionCount === null ? 'JSON draft needs validation' : `${questionCount} questions in this draft`}</span><span>Maximum 100 questions</span></div><details className="schema-details"><summary>Question format and source labels</summary><p>Use expected_doc_id or expected_source_key to identify the source. Evidence quotes must exactly match the normalized text. Unsupported questions remain diagnostic and do not measure generated-answer refusals.</p><pre>{JSON.stringify(config.dataset_schema, null, 2)}</pre></details></div></section>
      <section className="panel experiment-section"><div className="panel-heading"><div><h2><span className="step-number">3</span>Choose configurations</h2><p>Content token limits are checked against the actual embedding model.</p></div><button className="button secondary small" disabled={locked || candidates.length >= 12} onClick={() => setCandidates(values => [...values, { chunk_size: 200, overlap: 40, top_k: 3 }])}><Plus size={14} />Add configuration</button></div><div className="candidate-list">{candidates.map((candidate, index) => <div className="candidate-row" key={index}><span className="candidate-number">{String(index + 1).padStart(2, '0')}</span><label>Chunk tokens<input type="number" min={8} max={4096} value={candidate.chunk_size} step={1} onChange={event => update(index, 'chunk_size', Number(event.target.value))} disabled={locked} /></label><label>Overlap tokens<input type="number" min={0} max={Math.max(0, candidate.chunk_size - 1)} value={candidate.overlap} step={1} onChange={event => update(index, 'overlap', Number(event.target.value))} disabled={locked} /></label><label>Top K<input type="number" min={1} max={20} value={candidate.top_k} step={1} onChange={event => update(index, 'top_k', Number(event.target.value))} disabled={locked} /></label><button className="icon-button" aria-label={`Remove configuration ${index + 1}`} disabled={locked || candidates.length === 1} onClick={() => setCandidates(values => values.filter((_, current) => current !== index))}><X size={17} /></button></div>)}</div><div className="experiment-run"><p>The comparison uses temporary indexes. Applying settings to your sources is a separate action.</p><button className="button primary" disabled={locked || !selected.length || !candidates.length} onClick={run}>{busy ? <Spinner label="Running retrieval comparison…" /> : <><FlaskConical size={16} />Run comparison<ArrowRight size={15} /></>}</button></div>{busy && <p className="experiment-processing" role="status">Rechunking the selected corpus, building temporary indexes and retrieving evidence for each configuration.</p>}</section>
    </>}
    {report && <section className="panel experiment-results"><div className="panel-heading"><div><h2>Configuration comparison</h2><p>{report.corpus.length} source versions · Run <span className="mono">{report.run_id}</span></p></div><button className="button secondary small" onClick={download}><Download size={14} />Download JSON</button></div>{report.warnings.length > 0 && <div className="experiment-warnings">{report.warnings.map((warning, index) => <p key={index}>{warning}</p>)}</div>}<div className="table-scroll" role="region" aria-label="Experiment results table. Scroll horizontally to view all columns." tabIndex={0}><table><thead><tr><th>Tokens / overlap</th><th>Top K</th><th>Chunks</th><th>Source hit</th><th>Quote hit</th><th>MRR</th><th>Mean latency</th><th>Apply</th></tr></thead><tbody>{report.results.map((result, index) => <tr key={`${index}-${result.chunk_size}-${result.overlap}-${result.top_k}`}><td className="candidate-setting">{result.chunk_size} / {result.overlap}</td><td>{result.top_k}</td><td>{result.chunk_count}</td><td>{rate(result.source_hit_rate)}</td><td>{rate(result.evidence_hit_rate)}</td><td>{decimal(result.mean_reciprocal_rank)}</td><td>{result.mean_latency_ms === null ? '—' : `${result.mean_latency_ms.toFixed(1)} ms`}</td><td><button className="button secondary small" disabled={locked} onClick={() => apply(index)}>{applying === index ? <Spinner label="Applying…" /> : 'Apply chunk settings'}</button></td></tr>)}</tbody></table></div><div className="experiment-results-note"><p>{report.selection_note}</p><p>Source hit and MRR use answerable questions. Quote hit uses questions with evidence labels. “—” means there was no applicable denominator. Unsupported questions do not measure answer refusal or hallucination.</p><p>Applying rebuilds chunk size and overlap for the evaluated sources, one document at a time. Online/offline availability is preserved. Top K stays a query setting; no winning configuration is applied automatically. Thumbs feedback is separate from these gold-label scores.</p></div>{(applyProgress || applyResults.length > 0) && <div className="apply-results">{applyProgress && <div role="status"><Spinner label={applyProgress} /></div>}{applyResults.map(result => <article className={result.status} key={result.doc_id}>{result.status === 'completed' ? <Check size={15} /> : <X size={15} />}<div><strong>{result.name}</strong><p>{result.detail}</p></div></article>)}</div>}</section>}
  </>;
}
