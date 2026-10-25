import { useEffect, useState } from 'react';
import { RefreshCw, ShieldCheck } from 'lucide-react';
import { api, errorMessage } from '../api';
import { formatUtc } from '../adminData';
import type { ProfileResult } from '../types';
import { ErrorBanner, Spinner } from './Shared';

export default function Profile() {
  const [data, setData] = useState<ProfileResult | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [refresh, setRefresh] = useState(0);
  useEffect(() => {
    let active = true; setLoading(true); setError(null); setData(null);
    api.profile().then(result => { if (active) setData(result); }).catch(caught => { if (active) setError(errorMessage(caught)); }).finally(() => { if (active) setLoading(false); });
    return () => { active = false; };
  }, [refresh]);
  return <>
    <div className="page-heading"><div><span className="eyebrow">WORKSPACE / PROFILE</span><h1>Your profile<span className="heading-dot">.</span></h1><p>Your account, current session, and saved activity.</p></div><button className="button secondary" disabled={loading} onClick={() => setRefresh(value => value + 1)}><RefreshCw size={16} className={loading ? 'spin' : ''} />Refresh</button></div><ErrorBanner message={error} />
    {loading ? <div className="panel loading-box"><Spinner label="Loading your profile…" /></div> : data && <>
      <div className="admin-two-column"><section className="panel admin-section"><div className="panel-heading"><div><h2>Account details</h2><p>Only your own account is shown.</p></div></div><dl className="profile-facts"><div><dt>Email</dt><dd>{data.user.email}</dd></div><div><dt>Role</dt><dd>{data.user.role === 'admin' ? 'Administrator' : 'Employee'}</dd></div><div><dt>Department</dt><dd>{data.user.department || 'Not assigned'}</dd></div><div><dt>User ID</dt><dd className="mono">{data.user.user_id}</dd></div></dl></section>
        <section className="panel admin-section"><div className="panel-heading"><div><h2>Current session</h2><p>Details of the session you are using now.</p></div><ShieldCheck size={19} /></div><dl className="profile-facts"><div><dt>Authentication</dt><dd>Database-backed opaque cookie session</dd></div><div><dt>Session lifetime</dt><dd>{data.session.duration_hours} hours from sign-in</dd></div><div><dt>Expires at (UTC)</dt><dd>{formatUtc(data.session.expires_at)}</dd></div></dl><p className="admin-explanation profile-note">Signing out revokes this session. Activity does not extend its fixed expiry.</p></section></div>
      <section className="panel admin-section"><div className="panel-heading"><div><h2>Your saved activity</h2><p>Lifetime counts for your own account. Preview-only and unsaved failed requests are not included.</p></div></div><div className="profile-activity">{[
        ['Conversations', data.activity.conversations], ['Questions', data.activity.questions], ['Responses', data.activity.responses], ['Current thumbs up', data.activity.positive_feedback], ['Current thumbs down', data.activity.negative_feedback],
      ].map(([label, value]) => <div key={String(label)}><strong>{typeof value === 'number' ? value.toLocaleString() : value}</strong><span>{label}</span></div>)}</div></section>
      <p className="admin-explanation">This is a read-only profile. Identity, role, department, and password editing are not available here.</p>
    </>}
  </>;
}
