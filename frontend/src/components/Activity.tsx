import { useEffect, useState } from 'react';
import { Activity as ActivityIcon, RefreshCw } from 'lucide-react';
import { api, errorMessage } from '../api';
import type { AuditEvent } from '../types';
import { EmptyState, ErrorBanner, formatDate, Spinner } from './Shared';

export default function Activity() {
  const [events, setEvents] = useState<AuditEvent[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [refresh, setRefresh] = useState(0);
  useEffect(() => { let active = true; setLoading(true); setError(null); api.audit().then(data => { if (active) setEvents(data.events); }).catch(caught => { if (active) setError(errorMessage(caught)); }).finally(() => { if (active) setLoading(false); }); return () => { active = false; }; }, [refresh]);
  return <><div className="page-heading"><div><span className="eyebrow">WORKSPACE / ACTIVITY</span><h1>Activity log<span className="heading-dot">.</span></h1><p>A record of what changed and who changed it.</p></div><button className="button secondary" onClick={() => setRefresh(value => value + 1)} disabled={loading}><RefreshCw size={16} className={loading ? 'spin' : ''} />Refresh</button></div><ErrorBanner message={error} /><section className="panel"><div className="panel-heading"><div><h2>Recent workspace events</h2><p>Recorded actions from the server audit log.</p></div><ActivityIcon size={20} /></div>{loading && !events.length ? <div className="loading-box"><Spinner label="Loading activity…" /></div> : !events.length ? <EmptyState title="No activity recorded yet">Workspace actions will appear here as they happen.</EmptyState> : <div className="activity-list">{events.map(event => <article key={event.id}><span className="activity-dot" /><div className="activity-content"><strong>{event.action.replaceAll('_', ' ').replaceAll('.', ' · ')}</strong><p>{event.user_email || 'System'} · {event.entity_type}{event.entity_id && <span className="mono"> · {event.entity_id}</span>}</p>{event.details && Object.keys(event.details).length > 0 && <details><summary>View recorded details</summary><pre>{JSON.stringify(event.details, null, 2)}</pre></details>}</div><time>{formatDate(event.created_at)}</time></article>)}</div>}</section></>;
}
