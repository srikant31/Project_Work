import assert from 'node:assert/strict';
import { test } from 'node:test';
import { analyticsQuery, dateRangeError, documentQuery, formatUtc, versionError } from '../src/adminData.ts';

test('calendar filters allow open boundaries and same-day inclusive ranges', () => {
  assert.equal(dateRangeError('', ''), null);
  assert.equal(dateRangeError('2026-10-23', ''), null);
  assert.equal(dateRangeError('', '2026-10-23'), null);
  assert.equal(dateRangeError('2026-10-23', '2026-10-23'), null);
  assert.match(dateRangeError('2026-10-24', '2026-10-23'), /start date/);
});

test('invalid calendar dates and year zero are rejected instead of normalized', () => {
  for (const date of ['2026-02-29', '2024-02-30', '2026-13-01', '0000-01-01', '2026-1-1', 'nonsense']) {
    assert.match(dateRangeError(date, ''), /valid calendar/);
  }
  assert.equal(dateRangeError('2024-02-29', '2024-03-01'), null);
});

test('version filters reject zero, fractions, signs and unsafe integer conversion', () => {
  for (const version of ['0', '-1', '1.5', '1e2', '9007199254740993']) assert.match(versionError(version), /positive whole/);
  assert.equal(versionError(''), null);
  assert.equal(versionError('12'), null);
});

test('query builders omit empty date boundaries and retain document version selection', () => {
  assert.equal(analyticsQuery(), '');
  assert.equal(documentQuery(), '');
  const analytics = new URLSearchParams(analyticsQuery({ date_from: '2026-10-23' }));
  assert.equal(analytics.get('date_from'), '2026-10-23');
  assert.equal(analytics.has('date_to'), false);
  const documents = new URLSearchParams(documentQuery({ uploaded_to: '2026-10-23', version: 1 }));
  assert.equal(documents.has('uploaded_from'), false);
  assert.equal(documents.get('uploaded_to'), '2026-10-23');
  assert.equal(documents.get('version'), '1');
});

test('timestamps have explicit UTC semantics regardless of browser locale or source offset', () => {
  assert.equal(formatUtc('2026-10-23T02:00:00+02:00'), formatUtc('2026-10-23T00:00:00'));
  assert.match(formatUtc('2026-10-23T00:00:00Z'), /UTC/);
  assert.equal(formatUtc(null), 'Not recorded');
  assert.equal(formatUtc('bad-value'), 'Invalid recorded date');
});
