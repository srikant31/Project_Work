import assert from 'node:assert/strict';
import { after, beforeEach, test } from 'node:test';
import { ApiError, api, request } from '../src/api.ts';

const originalFetch = globalThis.fetch;
const originalWindow = globalThis.window;
let events;
beforeEach(() => {
  events = [];
  globalThis.window = { dispatchEvent: event => events.push(event.type) };
});
after(() => {
  globalThis.fetch = originalFetch;
  if (originalWindow === undefined) delete globalThis.window;
  else globalThis.window = originalWindow;
});

test('mutating requests carry cookies and the CSRF header without token storage', async () => {
  globalThis.fetch = async (url, options) => {
    assert.equal(url, '/api/search');
    assert.equal(options.credentials, 'same-origin');
    assert.equal(options.headers.get('X-Requested-With'), 'helpdesk-ui');
    assert.equal(options.headers.get('Content-Type'), 'application/json');
    assert.deepEqual(JSON.parse(options.body), { question: 'VPN access', top_k: 3 });
    return Response.json({ hits: [] });
  };
  assert.deepEqual(await api.search('VPN access', 3), { hits: [] });
});

test('multipart upload lets the browser supply its boundary', async () => {
  const form = new FormData();
  form.set('file', new Blob(['Network policy']), 'policy.txt');
  globalThis.fetch = async (_url, options) => {
    assert.equal(options.body, form);
    assert.equal(options.headers.get('Content-Type'), null);
    assert.equal(options.headers.get('X-Requested-With'), 'helpdesk-ui');
    return Response.json({ skipped: false });
  };
  assert.deepEqual(await api.upload(form), { skipped: false });
});

for (const endpoint of ['/auth/login', '/auth/me']) {
  test(`${endpoint} rejects 401 without expiring a newly created session`, async () => {
    globalThis.fetch = async () => Response.json({ detail: 'Not signed in' }, { status: 401 });
    await assert.rejects(request(endpoint), error => error instanceof ApiError && error.status === 401);
    assert.deepEqual(events, []);
  });
}

test('an authenticated endpoint returning 401 signals session expiration', async () => {
  globalThis.fetch = async () => Response.json({ detail: 'Session expired' }, { status: 401 });
  await assert.rejects(api.documents(), error => error.message === 'Session expired');
  assert.deepEqual(events, ['helpdesk:unauthorized']);
});

test('validation errors expose readable API details', async () => {
  globalThis.fetch = async () => Response.json({ detail: [{ msg: 'Chunk size must be positive' }, { msg: 'Overlap must be smaller' }] }, { status: 422 });
  await assert.rejects(request('/documents/example/reindex'), error => error.message === 'Chunk size must be positive; Overlap must be smaller');
});

test('network outages produce a clear local-backend message', async () => {
  globalThis.fetch = async () => { throw new TypeError('fetch failed'); };
  await assert.rejects(api.me(), error => error instanceof ApiError && error.status === 0 && error.message.includes('local backend'));
});

test('empty success responses do not fail JSON parsing', async () => {
  globalThis.fetch = async () => new Response(null, { status: 204 });
  assert.equal(await api.logout(), undefined);
});

test('context preview preserves server byte accounting without calling chat', async () => {
  const calls = [];
  globalThis.fetch = async (url, options) => {
    calls.push(url);
    assert.deepEqual(JSON.parse(options.body), { question: 'Reset a password?', top_k: 3 });
    return Response.json({ context_bytes: 718, context_tokens: null, token_count_method: 'utf8_bytes', messages: [] });
  };
  const result = await api.ragPreview('Reset a password?', 3);
  assert.equal(result.context_bytes, 718);
  assert.equal(result.context_tokens, null);
  assert.deepEqual(calls, ['/api/rag/preview']);
});

test('feedback payload is thumbs-only and does not send comments', async () => {
  globalThis.fetch = async (url, options) => {
    assert.equal(url, '/api/feedback');
    assert.deepEqual(JSON.parse(options.body), { response_id: 'response-id', rating: 'down' });
    return Response.json({ response_id: 'response-id', rating: 'down' });
  };
  assert.equal((await api.feedback('response-id', 'down')).rating, 'down');
});

test('experiment comparison preserves gold labels and does not apply settings', async () => {
  const questions = [{ question_id: 'q1', question: 'VPN requirements?', expected_doc_id: 'source-id', expected_source_key: null, evidence_quote: 'MFA is required.', answerable: true }];
  const candidates = [{ chunk_size: 150, overlap: 30, top_k: 5 }];
  const calls = [];
  globalThis.fetch = async (url, options) => {
    calls.push(url);
    assert.deepEqual(JSON.parse(options.body), { document_ids: ['source-id'], questions, configurations: candidates });
    return Response.json({ status: 'completed', results: [{ evidence_hit_rate: null }] });
  };
  const result = await api.runExperiment(['source-id'], questions, candidates);
  assert.equal(result.results[0].evidence_hit_rate, null);
  assert.deepEqual(calls, ['/api/experiments/run']);
});

test('admin review filters and pagination are sent together without mixing user turns', async () => {
  const calls = [];
  globalThis.fetch = async (url, options) => {
    calls.push(url);
    const parsed = new URL(url, 'http://localhost');
    assert.equal(parsed.pathname, '/api/admin/reviews');
    assert.equal(parsed.searchParams.get('user_id'), 'user-id');
    assert.equal(parsed.searchParams.get('conversation_id'), 'conversation-id');
    assert.equal(parsed.searchParams.get('feedback'), 'down');
    assert.equal(parsed.searchParams.get('status'), 'unanswered');
    assert.equal(parsed.searchParams.get('search'), 'VPN & payroll?');
    assert.equal(parsed.searchParams.get('limit'), '10');
    assert.equal(parsed.searchParams.get('offset'), '20');
    assert.equal(options.credentials, 'same-origin');
    assert.equal(options.method, undefined);
    return Response.json({ reviews: [], total: 0, limit: 10, offset: 20 });
  };
  await api.adminReviews({ user_id: 'user-id', conversation_id: 'conversation-id', feedback: 'down', status: 'unanswered', search: 'VPN & payroll?', limit: 10, offset: 20 });
  assert.equal(calls.length, 1);
});

test('all-user review requests omit optional identity filters and preserve unknown status', async () => {
  globalThis.fetch = async url => {
    const parsed = new URL(url, 'http://localhost');
    assert.equal(parsed.searchParams.has('user_id'), false);
    assert.equal(parsed.searchParams.has('conversation_id'), false);
    assert.equal(parsed.searchParams.get('status'), 'unknown');
    return Response.json({ reviews: [{ question: null, linkage: 'legacy_unlinked' }] });
  };
  const result = await api.adminReviews({ feedback: 'unrated', status: 'unknown', search: '', limit: 10, offset: 0 });
  assert.equal(result.reviews[0].question, null);
  assert.equal(result.reviews[0].linkage, 'legacy_unlinked');
});

test('admin user and conversation directory preserve search text and page offsets', async () => {
  const calls = [];
  globalThis.fetch = async url => {
    const parsed = new URL(url, 'http://localhost');
    calls.push(parsed);
    return Response.json({ total: 0 });
  };
  await api.adminUsers('it+support@example.com', 10, 30);
  await api.adminConversations('user / id', 20, 40);
  assert.equal(calls[0].pathname, '/api/admin/users');
  assert.equal(calls[0].searchParams.get('search'), 'it+support@example.com');
  assert.equal(calls[0].searchParams.get('offset'), '30');
  assert.equal(calls[1].pathname, '/api/admin/conversations');
  assert.equal(calls[1].searchParams.get('user_id'), 'user / id');
  assert.equal(calls[1].searchParams.get('offset'), '40');
});

test('response detail uses its exact encoded message ID', async () => {
  globalThis.fetch = async url => {
    assert.equal(url, '/api/admin/reviews/message%2F%3F%23');
    return Response.json({ review: { review_id: 'message/?#' }, context: [] });
  };
  assert.equal((await api.adminReview('message/?#')).review.review_id, 'message/?#');
});

test('document history filters are sent to the server and unfiltered listing stays compatible', async () => {
  const calls = [];
  globalThis.fetch = async url => { calls.push(url); return Response.json({ documents: [], summary: {}, scope: 'matching_versions' }); };
  await api.documents();
  const result = await api.documents({ uploaded_from: '2026-10-01', uploaded_to: '2026-10-23', version: 2 });
  assert.equal(calls[0], '/api/documents');
  const params = new URL(calls[1], 'http://localhost').searchParams;
  assert.equal(params.get('uploaded_from'), '2026-10-01');
  assert.equal(params.get('uploaded_to'), '2026-10-23');
  assert.equal(params.get('version'), '2');
  assert.equal(result.scope, 'matching_versions');
});

test('question-date review filters are retained together with rating and user filters', async () => {
  globalThis.fetch = async url => {
    const params = new URL(url, 'http://localhost').searchParams;
    assert.equal(params.get('user_id'), 'u1');
    assert.equal(params.get('feedback'), 'down');
    assert.equal(params.get('question_from'), '2026-10-01');
    assert.equal(params.get('question_to'), '2026-10-23');
    return Response.json({ reviews: [] });
  };
  await api.adminReviews({ user_id: 'u1', feedback: 'down', status: 'all', search: '', limit: 10, offset: 0, question_from: '2026-10-01', question_to: '2026-10-23' });
});

test('analytics preserves missing latency and sends only provided reporting boundaries', async () => {
  globalThis.fetch = async (url, options) => {
    assert.equal(url, '/api/analytics?date_to=2026-10-23');
    assert.equal(options.credentials, 'same-origin');
    return Response.json({ summary: { average_response_time_ms: null, timed_response_count: 0 } });
  };
  const result = await api.analytics({ date_to: '2026-10-23' });
  assert.equal(result.summary.average_response_time_ms, null);
  assert.equal(result.summary.timed_response_count, 0);
});

test('profile request does not let the browser select a different user', async () => {
  globalThis.fetch = async (url, options) => {
    assert.equal(url, '/api/profile');
    assert.equal(options.body, undefined);
    return Response.json({ user: { user_id: 'session-owner' }, session: { mechanism: 'opaque_cookie' } });
  };
  assert.equal((await api.profile()).user.user_id, 'session-owner');
});

test('category maintenance sends exact editable fields and handles no-content deletion', async () => {
  const calls = [];
  globalThis.fetch = async (url, options) => {
    calls.push({ url, method: options.method, body: options.body ? JSON.parse(options.body) : null });
    return options.method === 'DELETE' ? new Response(null, { status: 204 }) : Response.json({ category: { category_id: 'category/id', category_name: 'IT & Support' } });
  };
  await api.managedCategories();
  await api.createCategory('IT & Support', 'Team policies');
  await api.updateCategory('category/id', 'Technology', null);
  assert.equal(await api.deleteCategory('category/id'), undefined);
  assert.deepEqual(calls, [
    { url: '/api/categories', method: undefined, body: null },
    { url: '/api/categories', method: 'POST', body: { category_name: 'IT & Support', description: 'Team policies' } },
    { url: '/api/categories/category%2Fid', method: 'PATCH', body: { category_name: 'Technology', description: null } },
    { url: '/api/categories/category%2Fid', method: 'DELETE', body: null },
  ]);
});

test('a category reference conflict remains visible instead of being treated as success', async () => {
  globalThis.fetch = async () => Response.json({ detail: 'Category is referenced by stored document versions.' }, { status: 409 });
  await assert.rejects(api.deleteCategory('used-id'), error => error instanceof ApiError && error.status === 409 && error.message.includes('referenced'));
  assert.deepEqual(events, []);
});
