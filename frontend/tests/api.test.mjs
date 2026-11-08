import assert from 'node:assert/strict';
import { after, beforeEach, test } from 'node:test';
import { ApiError, api, request, requestTimeoutMs } from '../src/api.ts';

test('employee history uses owned paginated endpoints and encodes selected identity', async () => {
  const calls = [];
  globalThis.fetch = async url => { calls.push(url); return Response.json({ conversations: [], total: 0 }); };
  await api.conversations(20, 40);
  await api.conversation('conversation/id');
  assert.deepEqual(calls, ['/api/conversations?limit=20&offset=40', '/api/conversations/conversation%2Fid']);
});

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


test('negative feedback sends the explanation with its exact response', async () => {
  globalThis.fetch = async (url, options) => {
    assert.equal(url, '/api/feedback');
    assert.deepEqual(JSON.parse(options.body), { response_id: 'response-id', rating: 'down', comment: 'Missing important details: Please explain.' });
    return Response.json({ rating: 'down', comment: 'Missing important details: Please explain.' });
  };
  await api.feedback('response-id', 'down', 'Missing important details: Please explain.');
});
test('saved evidence and search mode use encoded API paths', async () => {
  const calls = [];
  globalThis.fetch = async (url) => { calls.push(url); return Response.json({}); };
  await api.evidence('response/id', 'chunk/id');
  await api.search('VPN-809', 3, 'hybrid');
  assert.deepEqual(calls, ['/api/responses/response%2Fid/evidence/chunk%2Fid', '/api/search?mode=hybrid']);
});


test('request deadlines allow indexing and chat longer than ordinary reads', () => {
  assert.equal(requestTimeoutMs('/conversations?limit=20'), 30000);
  assert.equal(requestTimeoutMs('/chat'), 180000);
  assert.equal(requestTimeoutMs('/search?mode=hybrid'), 120000);
  assert.equal(requestTimeoutMs('/documents/upload'), 900000);
  assert.equal(requestTimeoutMs('/documents/id/replace'), 900000);
  assert.equal(requestTimeoutMs('/documents/id/reindex'), 900000);
  assert.equal(requestTimeoutMs('/experiments/run'), 600000);
});
test('a stalled fetch times out and aborts without expiring the session', async () => {
  let signal;
  globalThis.fetch = (_url, options) => { signal = options.signal; return new Promise(() => {}); };
  await assert.rejects(request('/conversations', { timeoutMs: 10 }), error => error.status === 408 && error.message.includes('try again'));
  assert.equal(signal.aborted, true);
  assert.deepEqual(events, []);
});
test('deadline also covers stalled JSON body after HTTP success', async () => {
  globalThis.fetch = async () => ({ ok: true, status: 200, json: () => new Promise(() => {}) });
  await assert.rejects(request('/analytics', { timeoutMs: 10 }), error => error.status === 408);
});
test('a stalled error body also has a deadline', async () => {
  globalThis.fetch = async () => ({ ok: false, status: 503, json: () => new Promise(() => {}) });
  await assert.rejects(request('/analytics', { timeoutMs: 10 }), error => error.status === 408);
});
test('timed-out writes warn about uncertain completion and are not retried', async () => {
  let calls = 0;
  globalThis.fetch = () => { calls++; return new Promise(() => {}); };
  await assert.rejects(request('/chat', { method: 'POST', timeoutMs: 10 }), error => error.status === 408 && error.message.includes('may still finish') && error.message.includes('before submitting again'));
  assert.equal(calls, 1);
});
test('caller cancellation aborts an in-flight request distinctly from timeout', async () => {
  let signal;
  globalThis.fetch = (_url, options) => { signal = options.signal; return new Promise(() => {}); };
  const controller = new AbortController();
  const result = request('/conversations', { signal: controller.signal });
  controller.abort();
  await assert.rejects(result, error => error.status === 0 && error.message === 'Request cancelled.');
  assert.equal(signal.aborted, true);
});
test('already-cancelled requests do not contact the server', async () => {
  let calls = 0;
  globalThis.fetch = () => { calls++; return Response.json({}); };
  const controller = new AbortController(); controller.abort();
  await assert.rejects(request('/profile', { signal: controller.signal }), /Request cancelled/);
  assert.equal(calls, 0);
});
test('completed requests clear their deadline and caller listeners', async () => {
  let signal; let added = 0; let removed = 0;
  const controller = new AbortController();
  const add = controller.signal.addEventListener.bind(controller.signal);
  const remove = controller.signal.removeEventListener.bind(controller.signal);
  controller.signal.addEventListener = (...args) => { added++; return add(...args); };
  controller.signal.removeEventListener = (...args) => { removed++; return remove(...args); };
  globalThis.fetch = async (_url, options) => { signal = options.signal; return Response.json({ saved: true }); };
  assert.deepEqual(await request('/profile', { timeoutMs: 20, signal: controller.signal }), { saved: true });
  await new Promise(resolve => setTimeout(resolve, 30)); controller.abort();
  assert.equal(signal.aborted, false);
  assert.equal(added, 1); assert.equal(removed, 1);
});

test('a timeout during a 401 response body does not expire the user session', async () => {
  globalThis.fetch = async (_url, options) => ({ ok: false, status: 401, json: () => new Promise((_, reject) => options.signal.addEventListener('abort', () => reject(new DOMException('Aborted', 'AbortError')), { once: true })) });
  await assert.rejects(request('/conversations', { timeoutMs: 10 }), error => error.status === 408);
  await new Promise(resolve => setTimeout(resolve, 0));
  assert.deepEqual(events, []);
});
