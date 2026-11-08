import { useEffect, useRef, useState } from 'react';
import { ErrorBanner } from './Shared';

export default function NegativeFeedback({ onCancel, onSubmit }: { onCancel: () => void; onSubmit: (comment: string) => Promise<void> }) {
  const dialog = useRef<HTMLDialogElement>(null);
  const [reason, setReason] = useState(''); const [description, setDescription] = useState('');
  const [busy, setBusy] = useState(false); const [error, setError] = useState<string | null>(null);
  useEffect(() => { const node = dialog.current; node?.showModal(); return () => node?.close(); }, []);
  return <dialog ref={dialog} className="feedback-dialog" aria-label="Explain negative feedback" onCancel={event => { event.preventDefault(); if (!busy) onCancel(); }}>
    <form onSubmit={async event => { event.preventDefault(); setBusy(true); setError(null); try { await onSubmit(`${reason}${description.trim() ? `: ${description.trim()}` : ''}`); onCancel(); } catch (caught) { setError(caught instanceof Error ? caught.message : 'Could not save feedback'); } finally { setBusy(false); } }}>
      <h2>What went wrong?</h2><p>Choose a reason. Your administrator can review this feedback.</p><ErrorBanner message={error} />
      <label>Reason<select value={reason} onChange={event => setReason(event.target.value)} required disabled={busy} autoFocus><option value="">Choose a reason</option>{['Incorrect information', 'Did not answer my question', 'Missing important details', 'Wrong or irrelevant source', 'Other'].map(value => <option key={value}>{value}</option>)}</select></label>
      <label>Additional details (optional)<textarea rows={4} maxLength={500} value={description} onChange={event => setDescription(event.target.value)} disabled={busy} placeholder="What should the answer have explained? Avoid passwords or confidential information." /></label><small>{description.length}/500 characters</small>
      <div className="history-actions"><button type="button" className="button secondary" disabled={busy} onClick={onCancel}>Cancel</button><button type="submit" className="button primary" disabled={busy || !reason}>{busy ? 'Saving…' : 'Submit feedback'}</button></div>
    </form></dialog>;
}
