import { AlertCircle, Check, LoaderCircle, Radio, X } from 'lucide-react';
import type { ReactNode } from 'react';
import type { KnowledgeDocument } from '../types';

export function Spinner({ label = 'Loading…' }: { label?: string }) {
  return <span className="spinner-label"><LoaderCircle size={16} className="spin" />{label}</span>;
}
export function ErrorBanner({ message }: { message: string | null }) {
  return message ? <div className="message error" role="alert"><AlertCircle size={17} /><span>{message}</span></div> : null;
}
export function Notice({ message, onClose }: { message: string | null; onClose: () => void }) {
  return message ? <div className="message success" role="status"><Check size={17} /><span>{message}</span><button className="icon-button" aria-label="Dismiss notification" onClick={onClose}><X size={16} /></button></div> : null;
}
export function StatusBadge({ document }: { document: KnowledgeDocument }) {
  const state = document.status === 'active' ? document.is_online ? 'online' : 'offline' : document.status;
  return <span className={`badge ${state}`}><span className="status-dot" />{state === 'staging' ? 'Processing' : state.charAt(0).toUpperCase() + state.slice(1)}</span>;
}
export function EmptyState({ title, children }: { title: string; children: ReactNode }) {
  return <div className="empty-state"><span className="empty-icon"><Radio size={26} /></span><h3>{title}</h3><p>{children}</p></div>;
}
export function formatDate(value: string | null | undefined) {
  if (!value) return '—';
  const date = new Date(value.endsWith('Z') || /[+-]\d\d:\d\d$/.test(value) ? value : `${value}Z`);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString(undefined, { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' });
}
