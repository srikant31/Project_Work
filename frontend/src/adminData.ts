import type { DateRange, DocumentFilters } from './types';

function validDate(value: string) {
  if (!/^\d{4}-\d{2}-\d{2}$/.test(value) || Number(value.slice(0, 4)) < 1) return false;
  const parsed = new Date(`${value}T00:00:00Z`);
  return !Number.isNaN(parsed.getTime()) && parsed.toISOString().slice(0, 10) === value;
}

export function dateRangeError(from: string, to: string) {
  if ((from && !validDate(from)) || (to && !validDate(to))) return 'Choose valid calendar dates.';
  if (from && to && from > to) return 'The start date must be on or before the end date.';
  return null;
}

export function versionError(value: string) {
  if (!value) return null;
  return /^[1-9]\d*$/.test(value) && Number.isSafeInteger(Number(value))
    ? null : 'Document version must be a positive whole number.';
}

export function analyticsQuery(range: DateRange = {}) {
  const query = new URLSearchParams();
  if (range.date_from) query.set('date_from', range.date_from);
  if (range.date_to) query.set('date_to', range.date_to);
  return query.toString();
}

export function documentQuery(filters: DocumentFilters = {}) {
  const query = new URLSearchParams();
  if (filters.uploaded_from) query.set('uploaded_from', filters.uploaded_from);
  if (filters.uploaded_to) query.set('uploaded_to', filters.uploaded_to);
  if (filters.version !== undefined) query.set('version', String(filters.version));
  return query.toString();
}

export function formatUtc(value: string | null | undefined) {
  if (!value) return 'Not recorded';
  const normalized = value.endsWith('Z') || /[+-]\d\d:\d\d$/.test(value) ? value : `${value}Z`;
  const parsed = new Date(normalized);
  return Number.isNaN(parsed.getTime()) ? 'Invalid recorded date' : parsed.toLocaleString(undefined, {
    year: 'numeric', month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit', timeZone: 'UTC', timeZoneName: 'short',
  });
}
