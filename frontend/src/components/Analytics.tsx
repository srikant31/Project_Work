import { useEffect, useState } from 'react';
import type { FormEvent } from 'react';
import { BarChart3, RefreshCw } from 'lucide-react';
import { api, errorMessage } from '../api';
import { dateRangeError, formatUtc } from '../adminData';
import type { AnalyticsResult, DateRange } from '../types';
import { EmptyState, ErrorBanner, Spinner } from './Shared';

export default function Analytics() {
  const [data, setData] = useState<AnalyticsResult | null>(null);
  const [range, setRange] = useState<DateRange>({});
  const [from, setFrom] = useState('');
  const [to, setTo] = useState('');
  const [refresh, setRefresh] = useState(0);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [filterError, setFilterError] = useState<string | null>(null);
  useEffect(() => {
    let active = true; setLoading(true); setError(null); setData(null);
    api.analytics(range).then(result => { if (active) setData(result); }).catch(caught => { if (active) setError(errorMessage(caught)); }).finally(() => { if (active) setLoading(false); });
    return () => { active = false; };
  }, [range, refresh]);
  function apply(event: FormEvent) {
    event.preventDefault(); const problem = dateRangeError(from, to); setFilterError(problem);
    if (!problem) setRange({ date_from: from || undefined, date_to: to || undefined });
  }
  const summary = data?.summary;
  const metrics = summary ? [
    ['Questions', summary.total_questions, 'Saved user messages in the selected period'],
    ['Questions today', summary.questions_today, 'Today in UTC, within the selected period'],
    ['Questions this week', summary.questions_this_week, 'Monday onward in UTC, within the selected period'],
    ['Saved responses', summary.total_responses, 'Assistant messages in the selected period'],
    ['Thumbs up', summary.positive_feedback, 'Current owner votes on these responses'],
    ['Thumbs down', summary.negative_feedback, 'Current owner votes on these responses'],
    ['Not rated', summary.unrated_responses, 'No current owner vote recorded'],
    ['Average response time', summary.average_response_time_ms == null ? 'Not recorded' : `${summary.average_response_time_ms.toLocaleString(undefined, { maximumFractionDigits: 1 })} ms`, `${summary.timed_response_count} responses with known, nonnegative latency`],
    ['Insufficient evidence', summary.unsupported_responses, 'Explicitly recorded refusals; not a confidence score'],
    ['No useful retrieval', summary.no_useful_retrieval_responses, 'Explicit no_eligible_sources reason only'],
    ['Unknown outcomes', summary.unknown_outcome_responses, 'Missing historical outcome metadata'],
  ] : [];
  return <>
    <div className="page-heading"><div><span className="eyebrow">WORKSPACE / ANALYTICS</span><h1>Analytics<span className="heading-dot">.</span></h1><p>Understand saved usage, current feedback, and evidence coverage.</p></div><button className="button secondary" disabled={loading} onClick={() => setRefresh(value => value + 1)}><RefreshCw size={16} className={loading ? 'spin' : ''} />Refresh</button></div>
    <section className="panel admin-filter-panel" aria-labelledby="analytics-range-title"><div className="panel-heading"><div><h2 id="analytics-range-title">Reporting period</h2><p>Dates include the entire selected calendar days in UTC. Empty dates leave that boundary open.</p></div><BarChart3 size={19} /></div><form className="admin-date-form" onSubmit={apply}><label htmlFor="analytics-from">From (UTC)<input aria-label="Analytics from (UTC)" id="analytics-from" type="date" value={from} onChange={event => setFrom(event.target.value)} /></label><label htmlFor="analytics-to">Through (UTC)<input aria-label="Analytics through (UTC)" id="analytics-to" type="date" value={to} onChange={event => setTo(event.target.value)} /></label><button className="button primary" type="submit" disabled={loading}>Apply dates</button><button className="button secondary" type="button" disabled={loading} onClick={() => { setFrom(''); setTo(''); setFilterError(null); setRange({}); }}>All dates</button></form><div className="admin-panel-padding"><ErrorBanner message={filterError} /></div></section>
    <ErrorBanner message={error} />
    {loading ? <div className="panel loading-box"><Spinner label="Loading analytics…" /></div> : data && summary ? <>
      <p className="admin-scope-note" aria-live="polite">Showing {data.range.date_from || 'all earlier dates'} through {data.range.date_to || 'the latest saved activity'} · UTC · Generated {formatUtc(data.range.generated_at)}.</p>
      <p className="admin-explanation">Questions are grouped by the question date. Responses, latency, outcomes, and current thumbs votes are grouped by the response date. These are current ratings on saved responses, not a history of when votes were submitted. Unknown historical values remain unknown.</p>
      <div className="metrics-grid analytics-metrics">{metrics.map(([label, value, note]) => <div className="metric-card" key={String(label)}><div className="metric-top"><span>{label}</span></div><strong>{typeof value === 'number' ? value.toLocaleString() : value}</strong><small>{note}</small></div>)}</div>
      <section className="panel admin-section"><div className="panel-heading"><div><h2>Daily activity</h2><p>{data.range.daily_from} through {data.range.daily_to} · UTC · At most 31 days; summary totals above use the full selected period.</p></div></div>{data.daily_activity.length ? <div className="table-scroll" role="region" aria-label="Daily activity table" tabIndex={0}><table><thead><tr><th>Date (UTC)</th><th>Questions</th><th>Responses</th><th>Current thumbs up</th><th>Current thumbs down</th></tr></thead><tbody>{data.daily_activity.map(day => <tr key={day.date}><td>{day.date}</td><td>{day.questions}</td><td>{day.responses}</td><td>{day.positive_feedback}</td><td>{day.negative_feedback}</td></tr>)}</tbody></table></div> : <p className="admin-empty">No daily activity span is available for this period.</p>}</section>
      <div className="admin-two-column"><section className="panel admin-section"><div className="panel-heading"><div><h2>Frequently asked questions</h2><p>Top 10 · Grouped by case and whitespace, not meaning.</p></div></div>{data.frequent_questions.length ? <ol className="analytics-question-list">{data.frequent_questions.map((item, index) => <li key={`${index}-${item.question}`}><p>{item.question}</p><span>{item.count} {item.count === 1 ? 'question' : 'questions'}</span></li>)}</ol> : <EmptyState title="No saved questions">Saved user questions in the selected period will appear here.</EmptyState>}</section>
        <section className="panel admin-section"><div className="panel-heading"><div><h2>Document usage</h2><p>Top 10 · Distinct saved response/document pairs using retained context.</p></div></div>{data.document_usage.length ? <div className="table-scroll" role="region" aria-label="Document usage table" tabIndex={0}><table><thead><tr><th>Document</th><th>Responses</th></tr></thead><tbody>{data.document_usage.map(doc => <tr key={doc.doc_id}><td className="analytics-document"><strong>{doc.doc_name}</strong><small>Version {doc.version} · {doc.category_name || 'Uncategorized'}</small></td><td>{doc.responses}</td></tr>)}</tbody></table></div> : <EmptyState title="No retained source usage">Documents appear when a saved response has recorded context links.</EmptyState>}</section></div>
      <section className="panel admin-section"><div className="panel-heading"><div><h2>Insufficient-evidence examples</h2><p>Up to 10 saved responses explicitly marked as insufficient context. No confidence score is inferred.</p></div></div>{data.unsupported_questions.length ? <div className="analytics-refusals">{data.unsupported_questions.map(item => <details key={item.review_id}><summary><span>{item.question || 'Question link not recorded'}</span><small>{item.user_email} · {formatUtc(item.created_at)}</small></summary><p className="section-note">Reason: {item.reason || 'Not recorded'}</p><p className="review-text">{item.answer}</p><p className="mono">Response: {item.review_id}</p></details>)}</div> : <EmptyState title="No recorded refusals">No saved response in this period is explicitly marked as insufficient evidence.</EmptyState>}</section>
      <details className="panel analytics-definitions"><summary>How these metrics are calculated</summary><ul>{data.definitions.map((definition, index) => <li key={index}>{definition}</li>)}</ul><p>Preview-only and failed generation requests without saved messages are outside these metrics. No calibrated model-confidence measure is currently recorded.</p></details>
    </> : null}
  </>;
}
