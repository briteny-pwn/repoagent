import assert from 'node:assert/strict';
import test from 'node:test';
import { ApiClient, ApiError } from './api.ts';

test('login stores CSRF token and authenticated mutations include cookie and CSRF header', async () => {
  const calls: Array<{ url: string; init: RequestInit }> = [];
  const fetcher: typeof fetch = async (input, init) => {
    calls.push({ url: String(input), init: init ?? {} });
    return new Response(JSON.stringify(calls.length === 1 ? { authenticated: true, csrf_token: 'csrf-value' } : { id: '1', status: 'queued' }), { status: 200, headers: { 'Content-Type': 'application/json' } });
  };
  const api = new ApiClient(fetcher);
  await api.login('password');
  await api.createTask('分析仓库', 'request-1');
  assert.equal(calls[0].url, '/api/auth/login');
  assert.equal(calls[0].init.credentials, 'include');
  assert.equal(new Headers(calls[0].init.headers).has('X-CSRF-Token'), false);
  assert.equal(calls[1].url, '/api/tasks');
  assert.equal(calls[1].init.credentials, 'include');
  assert.equal(new Headers(calls[1].init.headers).get('X-CSRF-Token'), 'csrf-value');
  assert.deepEqual(JSON.parse(String(calls[1].init.body)), { prompt: '分析仓库', request_id: 'request-1' });
});

test('restored session supplies CSRF token to settings updates', async () => {
  const calls: RequestInit[] = [];
  const api = new ApiClient(async (_input, init) => {
    calls.push(init ?? {});
    return new Response(JSON.stringify(calls.length === 1 ? { authenticated: true, csrf_token: 'restored' } : {}), { status: 200 });
  });
  await api.session();
  await api.updateModel({ base_url: 'https://example.com/v1', model: 'model-x', max_steps: 6 });
  assert.equal(new Headers(calls[1].headers).get('X-CSRF-Token'), 'restored');
  assert.deepEqual(JSON.parse(String(calls[1].body)), { base_url: 'https://example.com/v1', model: 'model-x', max_steps: 6 });
});

test('401 clears session and server details become readable errors', async () => {
  let count = 0;
  const api = new ApiClient(async () => {
    count += 1;
    return count === 1
      ? new Response(JSON.stringify({ authenticated: true, csrf_token: 'token' }), { status: 200 })
      : new Response(JSON.stringify({ detail: '登录已过期' }), { status: 401 });
  });
  await api.session();
  await assert.rejects(api.tasks(), (error: unknown) => error instanceof ApiError && error.status === 401 && error.message === '登录已过期');
  assert.equal(api.csrfToken, '');
});

test('draft edits and confirmation carry the current version', async () => {
  const calls: Array<{ url: string; init: RequestInit }> = [];
  const api = new ApiClient(async (input, init) => {
    calls.push({ url: String(input), init: init ?? {} });
    return new Response(JSON.stringify({}), { status: 200 });
  });
  await api.editDraft('draft/one', 3, '标题', '正文');
  await api.confirmDraft('draft/one', 4);
  assert.equal(calls[0].url, '/api/drafts/draft%2Fone');
  assert.deepEqual(JSON.parse(String(calls[0].init.body)), { version: 3, title: '标题', body: '正文' });
  assert.deepEqual(JSON.parse(String(calls[1].init.body)), { version: 4 });
});

test('default browser fetch is called without the client as its receiver', async () => {
  const original = globalThis.fetch;
  let receiver: unknown;
  globalThis.fetch = function (this: unknown) {
    receiver = this;
    return Promise.resolve(new Response('[]', { status: 200 }));
  } as typeof fetch;
  try {
    await new ApiClient().tasks();
    assert.equal(receiver, undefined);
  } finally {
    globalThis.fetch = original;
  }
});

test('node activation uses versioned contract route and PUT method', async () => {
  let method = '';
  const api = new ApiClient(async (_input, init) => {
    method = init?.method ?? '';
    return new Response(JSON.stringify({ id: 'job-1', status: 'queued' }), { status: 200 });
  });
  await api.selectNode('节点一');
  assert.equal(method, 'PUT');
});

test('subscription refresh returns settings directly instead of a task', async () => {
  const settings = { subscription: { nodes: [{ name: 'one', type: 'trojan' }] } };
  const api = new ApiClient(async (url, init) => {
    assert.equal(url, '/api/settings/subscription/refresh');
    assert.equal(init?.method, 'POST');
    return new Response(JSON.stringify(settings));
  });
  assert.deepEqual(await api.refreshSubscription(), settings);
});
