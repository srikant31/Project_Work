import { useRef, useState } from 'react';
import { api, errorMessage } from '../api';
import { ErrorBanner, Spinner } from './Shared';

export default function EvidenceButton({ responseId, chunkId }: { responseId: string; chunkId: string }) {
  const dialog = useRef<HTMLDialogElement>(null);
  const [data, setData] = useState<Awaited<ReturnType<typeof api.evidence>> | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  async function open() {
    setData(null); setError(null); setBusy(true); dialog.current?.showModal();
    try { setData(await api.evidence(responseId, chunkId)); }
    catch (caught) { setError(errorMessage(caught)); }
    finally { setBusy(false); }
  }
  function highlighted(text: string, quotes: string[]) {
    const matches: { start: number; end: number }[] = [];
    for (const quote of quotes) { const start = quote ? text.indexOf(quote) : -1; if (start >= 0) matches.push({ start, end: start + quote.length }); }
    matches.sort((a, b) => a.start - b.start);
    const parts = []; let cursor = 0;
    for (const match of matches) { if (match.start < cursor) continue; parts.push(text.slice(cursor, match.start), <mark key={match.start}>{text.slice(match.start, match.end)}</mark>); cursor = match.end; }
    parts.push(text.slice(cursor)); return parts;
  }
  return <><button type="button" className="text-button" onClick={open}>View evidence</button>
    <dialog ref={dialog} className="evidence-dialog" aria-label="Saved source evidence"><button type="button" className="button secondary small" onClick={() => dialog.current?.close()} autoFocus>Close</button>
      <ErrorBanner message={error} />{busy ? <Spinner label="Loading saved evidence…" /> : data && <><h2>{data.doc_name}</h2><p>Version {data.version} · {data.page_number ? `Page ${data.page_number}` : data.section_heading || 'Document text'}</p><p className="section-note">Exact chunk retained for this response. Highlighted text was quoted in the answer.</p><blockquote className="saved-chunk">{highlighted(data.text, data.quotes)}</blockquote><small>Chunk: {data.chunk_id}</small></>}
    </dialog></>;
}
