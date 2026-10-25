import assert from 'node:assert/strict';
import { test } from 'node:test';
import { parseGoldQuestions, remapGoldDocumentIds } from '../src/experimentData.ts';

const gold = [
  { question_id: 'q1', question: 'How to reset a password?', expected_doc_id: 'old-version', expected_source_key: 'password.txt', evidence_quote: 'Visit the IT desk.', answerable: true },
  { question_id: 'q2', question: 'VPN access?', expected_doc_id: 'other-source', expected_source_key: 'vpn.txt', evidence_quote: 'Use MFA.', answerable: true },
  { question_id: 'q3', question: 'Unsupported request?', expected_doc_id: null, expected_source_key: null, evidence_quote: null, answerable: false },
];

test('explicit apply updates only corresponding editable document labels', () => {
  const frozenReport = structuredClone({ corpus: [{ doc_id: 'old-version' }], questions: gold });
  const original = JSON.stringify(gold, null, 2);
  const result = remapGoldDocumentIds(original, new Map([['old-version', 'new-version']]));
  const updated = JSON.parse(result.dataset);
  assert.equal(result.changed, 1);
  assert.equal(result.warning, null);
  assert.equal(updated[0].expected_doc_id, 'new-version');
  assert.equal(updated[0].expected_source_key, 'password.txt');
  assert.equal(updated[0].evidence_quote, 'Visit the IT desk.');
  assert.deepEqual(updated.slice(1), gold.slice(1));
  assert.equal(gold[0].expected_doc_id, 'old-version');
  assert.deepEqual(frozenReport, { corpus: [{ doc_id: 'old-version' }], questions: gold });
});

test('invalid or partial edits are preserved and produce an actionable warning', () => {
  for (const draft of ['[{"question_id":', '[{"question_id":"unfinished"}]']) {
    const result = remapGoldDocumentIds(draft, new Map([['old-version', 'new-version']]));
    assert.equal(result.dataset, draft);
    assert.equal(result.changed, 0);
    assert.match(result.warning, /kept unchanged/);
  }
});

test('unrelated edits and formatting stay unchanged when no labels match', () => {
  const original = '  ' + JSON.stringify(gold);
  assert.equal(remapGoldDocumentIds(original, new Map([['unrelated', 'new-version']])).dataset, original);
});

test('question parsing accepts a UTF-8 BOM but rejects duplicate question IDs', () => {
  assert.equal(parseGoldQuestions('\uFEFF' + JSON.stringify(gold)).length, 3);
  assert.throws(() => parseGoldQuestions(JSON.stringify([gold[0], gold[0]])), /more than once/);
});
