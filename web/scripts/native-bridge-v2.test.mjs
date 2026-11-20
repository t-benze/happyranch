// Focused Node 24 checks for THR-387 S5-W1 (web consumption of native bridge
// v2: target generation, stale drop, 409/429/499/busy handling, W-1/W-2).
// No Vitest or Product E2E selection. Run from the repository root:
//   node --experimental-transform-types --test web/scripts/native-bridge-v2.test.mjs
import { registerHooks } from 'node:module';
import { existsSync } from 'node:fs';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { dirname, resolve as resolvePath } from 'node:path';
import { test, beforeEach } from 'node:test';
import assert from 'node:assert/strict';

const SRC = resolvePath(dirname(fileURLToPath(import.meta.url)), '..', 'src');

registerHooks({
  resolve(specifier, context, nextResolve) {
    let base = null;
    if (specifier.startsWith('@/')) base = resolvePath(SRC, specifier.slice(2));
    else if ((specifier.startsWith('./') || specifier.startsWith('../')) && context.parentURL?.endsWith('.ts')) {
      base = resolvePath(dirname(fileURLToPath(context.parentURL)), specifier);
    }
    if (base && !/\.[cm]?[jt]s$/.test(base)) {
      for (const candidate of [`${base}.ts`, `${base}/index.ts`]) {
        if (existsSync(candidate)) return nextResolve(pathToFileURL(candidate).href, context);
      }
    }
    return nextResolve(specifier, context);
  },
});

class MemoryStorage {
  #m = new Map();
  getItem(k) { return this.#m.has(k) ? this.#m.get(k) : null; }
  setItem(k, v) { this.#m.set(k, String(v)); }
  removeItem(k) { this.#m.delete(k); }
  clear() { this.#m.clear(); }
}
globalThis.sessionStorage = new MemoryStorage();
globalThis.window = globalThis;
globalThis.document = { hidden: false, addEventListener() {}, removeEventListener() {} };
const BROWSER_LOCATION = { protocol: 'http:', origin: 'http://127.0.0.1:8765' };
const APP_LOCATION = { protocol: 'happyranch-app:', origin: 'happyranch-app://app' };
Object.defineProperty(globalThis, 'location', { configurable: true, writable: true, value: BROWSER_LOCATION });

const recovery = await import('../src/lib/nativeRecovery.ts');
const auth = await import('../src/lib/auth.ts');
const client = await import('../src/lib/api/client.ts');
const artifacts = await import('../src/lib/api/artifacts.ts');
const tasks = await import('../src/lib/api/tasks.ts');
const { subscribeSSE } = await import('../src/lib/api/sse.ts');
const target = await import('../src/lib/native/target.ts');
const notices = await import('../src/lib/native/notices.ts');
const appStatus = await import('../src/lib/native/appStatus.ts');
const { attachNativeQueryClient } = await import('../src/lib/native/queryClient.ts');
const { QueryClient, QueryObserver } = await import('@tanstack/react-query');

const { ApiError, NativeRecoveryError, StaleTargetError, NotSentError, ResultUnknownError, ThrottledError, request } = client;

// --- fetch stub --------------------------------------------------------------
let calls = [];
let queue = [];
function json(status, body) {
  return () => new Response(body === undefined ? null : JSON.stringify(body), {
    status, headers: { 'Content-Type': 'application/json' },
  });
}
globalThis.fetch = async (url, init = {}) => {
  const headers = new Headers(init.headers);
  calls.push({ url: String(url), method: init.method ?? 'GET', target: headers.get('X-HappyRanch-Target'), signal: init.signal });
  const next = queue.shift();
  if (!next) throw new Error(`unexpected fetch ${url}`);
  return next(init);
};
const PATHS = () => calls.map((c) => new URL(c.url, 'http://h').pathname);

// --- mock bridge ---------------------------------------------------------------
function state(generation, overrides = {}) {
  return {
    schema: 2,
    generation,
    target: { kind: 'local', id: 'local', name: 'This computer' },
    state: 'ready',
    incompatible: null,
    local: { supported: true, mode: 'service', running: 'running', prompt_base_url: 'http://127.0.0.1:8765/api/v1' },
    candidate: null,
    access_unsaved: false,
    locale: { current: 'en', persisted: true },
    ...overrides,
  };
}
function makeBridge(initial) {
  const bridge = {
    status: initial,
    statusCalls: 0,
    handlers: new Set(),
    call(command, data) {
      assert.equal(command, 'app.status');
      assert.equal(data, '{}');
      bridge.statusCalls += 1;
      return Promise.resolve(JSON.stringify(bridge.status));
    },
    on(event, handler) {
      assert.equal(event, 'app.stateChanged');
      bridge.handlers.add(handler);
      return () => bridge.handlers.delete(handler);
    },
    emit(next) {
      bridge.status = next;
      for (const h of [...bridge.handlers]) h(JSON.stringify(next));
    },
  };
  return bridge;
}
function desktop(initial = state('g1')) {
  globalThis.location = APP_LOCATION;
  const bridge = makeBridge(initial);
  target.__setNativeBridgeForTests(bridge);
  return bridge;
}

beforeEach(() => {
  calls = [];
  queue = [];
  globalThis.location = BROWSER_LOCATION;
  target.__setNativeBridgeForTests(null);
  auth.__resetTokenCacheForTests();
  recovery.__resetNativeRecoveryForTests();
  notices.__resetNativeNoticeForTests();
  sessionStorage.setItem('happyranch.token', 'tok-1');
});

// --- appState parsing ------------------------------------------------------------
test('parseAppState accepts the spec §12 valid examples and rejects contract violations', () => {
  assert.ok(appStatus.parseAppState(JSON.stringify(state('g12'))));
  assert.ok(appStatus.parseAppState(state('g13', {
    state: 'incompatible',
    incompatible: { side: 'host', host_contract: null, client_contract: 1, min_daemon: 1 },
    locale: { current: 'zh-CN', persisted: true },
  })));
  const remote = state('g14', {
    target: { kind: 'remote', id: 'saved-1', name: 'Studio Host' },
    local: { supported: true, mode: 'service', running: 'stopped', prompt_base_url: null },
    candidate: { kind: 'remote', id: 'saved-2', name: 'Work Host', phase: 'signing_in', deadline: '2026-11-21T03:30:00+08:00' },
    access_unsaved: true,
    locale: { current: 'en', persisted: false },
  });
  assert.ok(appStatus.parseAppState(remote));
  // A remote target never discloses an address.
  assert.equal(appStatus.parseAppState({ ...remote, local: { ...remote.local, prompt_base_url: 'http://10.0.0.2:8765/api/v1' } }), null);
  assert.equal(appStatus.parseAppState({ ...state('g1'), extra: true }), null);
  assert.equal(appStatus.parseAppState({ ...state('g1'), schema: 1 }), null);
  assert.equal(appStatus.parseAppState(state('')), null);
  assert.equal(appStatus.parseAppState(state('x'.repeat(33))), null);
  assert.equal(appStatus.parseAppState(state('g1', { state: 'no_target' })), null); // target must be null
  assert.equal(appStatus.parseAppState(state('g1', { state: 'incompatible' })), null); // incompatible required
  assert.equal(appStatus.parseAppState(state('g1', {
    state: 'incompatible', incompatible: { side: 'app', host_contract: null, client_contract: 1, min_daemon: 1 },
  })), null); // null host_contract requires side host
  assert.equal(appStatus.parseAppState('not json'), null);
});

test('deriveConnectionMenuState is presentation-only (names, phase, badge; no address)', () => {
  const menu = appStatus.deriveConnectionMenuState(state('g14', {
    target: { kind: 'remote', id: 'saved-1', name: 'Studio Host' },
    local: { supported: true, mode: 'service', running: 'stopped', prompt_base_url: null },
    candidate: { kind: 'local', id: 'local', name: 'This computer', phase: 'ready_to_switch', deadline: null },
    access_unsaved: true,
  }));
  assert.deepEqual(menu, {
    target: { kind: 'remote', name: 'Studio Host' },
    state: 'ready',
    ready: true,
    localActive: false,
    localRunning: 'stopped',
    localSupported: true,
    accessUnsaved: true,
    candidate: { kind: 'local', name: 'This computer', phase: 'ready_to_switch' },
    incompatibleSide: null,
  });
});

// --- browser mode unchanged --------------------------------------------------------
test('browser: constant generation "browser", no target header on any request', async () => {
  assert.equal(target.isNativeTarget(), false);
  assert.equal(target.currentGeneration(), 'browser');
  queue.push(json(200, { ok: 1 }));
  await request('/orgs');
  queue.push(json(201, { name: 'a' }));
  await artifacts.uploadArtifact('demo', { file: new File(['x'], 'a.txt'), agent: 'founder' });
  assert.deepEqual(calls.map((c) => c.target), [null, null]);
  assert.equal(target.getPromptBaseUrl(), 'http://127.0.0.1:8765/api/v1');
});

// --- desktop header + seeding --------------------------------------------------------
test('desktop: requests wait for app.status, then echo the native generation; bootstrap has no header', async () => {
  desktop(state('g1'));
  sessionStorage.removeItem('happyranch.token');
  queue.push(json(200, { token: 'tok-9' })); // bootstrap
  queue.push(json(200, { ok: 1 }));
  await request('/orgs');
  assert.deepEqual(PATHS(), ['/api/v1/auth/bootstrap', '/api/v1/orgs']);
  assert.equal(calls[0].target, null, 'GET /auth/bootstrap is exempt');
  assert.equal(calls[1].target, 'g1');
  assert.equal(target.getPromptBaseUrl(), 'http://127.0.0.1:8765/api/v1');
});

test('desktop: every scheme API path stamps the captured generation (client, artifacts, tasks upload)', async () => {
  const bridge = desktop(state('g1'));
  queue.push(json(200, {}));
  await request('/x', { method: 'POST', body: {} });
  bridge.emit(state('g2'));
  queue.push(json(200, { artifacts: [] }));
  await artifacts.listArtifacts('demo');
  queue.push(json(200, {}));
  await artifacts.deleteArtifact('demo', 'a.txt');
  queue.push(() => new Response('blob', { status: 200 }));
  globalThis.URL.createObjectURL = () => 'blob:x';
  globalThis.URL.revokeObjectURL = () => {};
  globalThis.document.createElement = () => ({ click() {} });
  await artifacts.downloadArtifact('demo', 'a.txt');
  queue.push(json(200, { storage_key: 'k' }));
  await tasks.uploadTaskAttachment('demo', new File(['x'], 'a.txt'));
  assert.deepEqual(calls.map((c) => c.target), ['g1', 'g2', 'g2', 'g2', 'g2']);
});

test('desktop: no generation known -> nothing is sent (fail closed)', async () => {
  globalThis.location = APP_LOCATION;
  target.__setNativeBridgeForTests({ call: () => Promise.reject(new Error('revoked')), on: () => () => {} });
  const read = await request('/x').catch((e) => e);
  assert.ok(read instanceof NativeRecoveryError);
  const write = await request('/x', { method: 'POST', body: {} }).catch((e) => e);
  assert.ok(write instanceof NotSentError);
  assert.equal(calls.length, 0);
  assert.equal(notices.getNativeNotice(), 'not_sent');
});

test('desktop: remote target -> prompt base URL is null (never a remote address)', async () => {
  desktop(state('g5', {
    target: { kind: 'remote', id: 'saved-1', name: 'Studio Host' },
    local: { supported: true, mode: 'service', running: 'stopped', prompt_base_url: null },
  }));
  await target.captureTarget();
  assert.equal(target.getPromptBaseUrl(), null);
});

// --- stale drop ----------------------------------------------------------------
test('stale: a GET answered after a generation change is dropped with StaleTargetError (no state, no notice)', async () => {
  const bridge = desktop(state('g1'));
  await target.captureTarget();
  queue.push(() => {
    bridge.emit(state('g2'));
    return json(200, { stale: true })();
  });
  const err = await request('/orgs').catch((e) => e);
  assert.ok(err instanceof StaleTargetError);
  assert.equal(recovery.getNativeRecoveryState(), null);
  assert.equal(notices.getNativeNotice(), null);
});

test('stale: a mutation interrupted by a switch is never silent: ResultUnknownError, no retry', async () => {
  const bridge = desktop(state('g1'));
  await target.captureTarget();
  queue.push(() => {
    bridge.emit(state('g2'));
    throw new TypeError('stream error');
  });
  const err = await request('/x', { method: 'POST', body: {} }).catch((e) => e);
  assert.ok(err instanceof ResultUnknownError);
  assert.equal(calls.length, 1);
  assert.equal(notices.getNativeNotice(), 'result_unknown');
});

test('499 cancelled after forwarding on a mutation -> ResultUnknownError, single send', async () => {
  desktop(state('g1'));
  queue.push(json(499, { detail: { code: 'cancelled' } }));
  const err = await request('/x', { method: 'PUT', body: {} }).catch((e) => e);
  assert.ok(err instanceof ResultUnknownError);
  assert.equal(err.status, 499);
  assert.equal(calls.length, 1);
  assert.equal(notices.getNativeNotice(), 'result_unknown');
});

// --- 409 target_changed ---------------------------------------------------------------
test('W-2b: 409 target_changed on a mutation is never auto-retried; re-reads app.status; Not sent', async () => {
  const bridge = desktop(state('g1'));
  await target.captureTarget();
  const before = bridge.statusCalls;
  queue.push(json(409, { detail: { code: 'target_changed' } }));
  const err = await request('/x', { method: 'POST', body: { draft: 'keep me' } }).catch((e) => e);
  assert.ok(err instanceof NotSentError);
  assert.ok(err instanceof NativeRecoveryError && err.status === 409);
  assert.equal(calls.length, 1, 'mutation sent exactly once');
  assert.equal(bridge.statusCalls, before + 1, 'app.status re-read');
  assert.equal(notices.getNativeNotice(), 'not_sent');
  assert.equal(recovery.getNativeRecoveryState(), null, 'not page-blocking');
});

test('W-2b: artifact delete/upload and task upload 409 are never auto-retried either', async () => {
  desktop(state('g1'));
  queue.push(json(409, { detail: { code: 'target_changed' } }));
  assert.ok((await artifacts.deleteArtifact('demo', 'a').catch((e) => e)) instanceof NotSentError);
  queue.push(json(409, { detail: { code: 'target_changed' } }));
  assert.ok((await artifacts.uploadArtifact('demo', { file: new File(['x'], 'a'), agent: 'founder' }).catch((e) => e)) instanceof NotSentError);
  queue.push(json(409, { detail: { code: 'target_changed' } }));
  assert.ok((await tasks.uploadTaskAttachment('demo', new File(['x'], 'a')).catch((e) => e)) instanceof NotSentError);
  assert.equal(calls.length, 3);
});

test('409 on a GET with unchanged generation refetches exactly once', async () => {
  desktop(state('g1'));
  queue.push(json(409, { detail: { code: 'target_changed' } }));
  queue.push(json(200, { ok: true }));
  assert.deepEqual(await request('/orgs'), { ok: true });
  assert.equal(calls.length, 2);
  calls = [];
  queue.push(json(409, { detail: { code: 'target_changed' } }));
  queue.push(json(409, { detail: { code: 'target_changed' } }));
  const err = await request('/orgs').catch((e) => e);
  assert.ok(err instanceof NativeRecoveryError);
  assert.equal(calls.length, 2, 'one refetch only');
});

test('409 on a GET after a switch: dropped as stale (the new generation owner refetches)', async () => {
  const bridge = desktop(state('g1'));
  await target.captureTarget();
  bridge.status = state('g2'); // native already switched; app.status re-read reveals it
  queue.push(json(409, { detail: { code: 'target_changed' } }));
  const err = await request('/orgs').catch((e) => e);
  assert.ok(err instanceof StaleTargetError);
  assert.equal(calls.length, 1);
  assert.equal(target.currentGeneration(), 'g2');
});

test('browser: 409 target_changed keeps the S1b behavior (recovery error + state)', async () => {
  queue.push(json(409, { detail: { code: 'target_changed' } }));
  const err = await request('/x', { method: 'POST', body: {} }).catch((e) => e);
  assert.ok(err instanceof NativeRecoveryError && !(err instanceof NotSentError));
  assert.equal(calls.length, 1);
  assert.deepEqual(recovery.getNativeRecoveryState(), { code: 'target_changed' });
});

// --- 429 / busy (W-1) -------------------------------------------------------------------
test('429 too_many_requests: GET retries with backoff (non-blocking), success clears the notice', async () => {
  desktop(state('g1'));
  queue.push(json(429, { detail: { code: 'too_many_requests' } }));
  queue.push(() => {
    assert.equal(notices.getNativeNotice(), 'throttled');
    return json(200, { ok: 1 })();
  });
  assert.deepEqual(await request('/orgs'), { ok: 1 });
  assert.equal(calls.length, 2);
  assert.equal(notices.getNativeNotice(), null);
  assert.equal(recovery.getNativeRecoveryState(), null);
});

test('429 on a mutation: ThrottledError at once, never retried, distinct from busy', async () => {
  desktop(state('g1'));
  queue.push(json(429, { detail: { code: 'too_many_requests' } }));
  const err = await request('/x', { method: 'POST', body: {} }).catch((e) => e);
  assert.ok(err instanceof ThrottledError && err.status === 429 && !(err instanceof NativeRecoveryError));
  assert.equal(calls.length, 1);
  assert.equal(notices.getNativeNotice(), 'throttled');
});

test('W-1: 503 busy is transient: no page-blocking state, GET retries, success clears', async () => {
  queue.push(json(503, { detail: { code: 'busy' } }));
  queue.push(() => {
    assert.equal(recovery.getNativeRecoveryState(), null, 'never page-blocking');
    assert.equal(notices.getNativeNotice(), 'busy');
    return json(200, { ok: 1 })();
  });
  assert.deepEqual(await request('/orgs'), { ok: 1 });
  assert.equal(notices.getNativeNotice(), null);
  // A mutation meeting busy is not retried and is still not page-blocking.
  queue.push(json(503, { detail: { code: 'busy' } }));
  const err = await request('/x', { method: 'POST', body: {} }).catch((e) => e);
  assert.ok(err instanceof NativeRecoveryError && err.code === 'busy');
  assert.equal(calls.length, 3);
  assert.equal(recovery.getNativeRecoveryState(), null);
});

test('W-1: artifacts list (direct fetch) also treats busy as transient and retries', async () => {
  queue.push(json(503, { detail: { code: 'busy' } }));
  queue.push(json(200, { artifacts: [] }));
  assert.deepEqual(await artifacts.listArtifacts('demo'), { artifacts: [] });
  assert.equal(calls.length, 2);
  assert.equal(recovery.getNativeRecoveryState(), null);
  assert.equal(notices.getNativeNotice(), null);
});

test('W-1: busy on bootstrap and in app.status is a notice, not a page state', async () => {
  sessionStorage.removeItem('happyranch.token');
  queue.push(json(503, { detail: { code: 'busy' } }));
  await auth.getToken().catch(() => {});
  assert.equal(recovery.getNativeRecoveryState(), null);
  assert.equal(notices.getNativeNotice(), 'busy');
  notices.__resetNativeNoticeForTests();
  const bridge = desktop(state('g1'));
  await target.captureTarget();
  bridge.emit(state('g1', { state: 'busy' }));
  assert.equal(recovery.getNativeRecoveryState(), null);
  assert.equal(notices.getNativeNotice(), 'busy');
  bridge.emit(state('g1'));
  assert.equal(notices.getNativeNotice(), null);
});

// --- W-2a + status-derived recovery ------------------------------------------------------
test('W-2a: recovery state clears on a later successful forwarded response', async () => {
  queue.push(json(503, { detail: { code: 'target_unreachable' } }));
  await request('/orgs').catch(() => {});
  assert.deepEqual(recovery.getNativeRecoveryState(), { code: 'target_unreachable' });
  queue.push(json(200, { ok: 1 }));
  await request('/orgs');
  assert.equal(recovery.getNativeRecoveryState(), null);
});

test('W-2a: a stale success does not clear (or revive) the current recovery state', async () => {
  const bridge = desktop(state('g1'));
  await target.captureTarget();
  queue.push(() => {
    bridge.emit(state('g2', { state: 'unreachable' }));
    return json(200, { ok: 1 })();
  });
  await request('/orgs').catch(() => {});
  assert.deepEqual(recovery.getNativeRecoveryState(), { code: 'target_unreachable' });
});

test('app.status drives the page state: unreachable/incompatible set it, ready clears it', async () => {
  const bridge = desktop(state('g1'));
  await target.captureTarget();
  bridge.emit(state('g2', { state: 'unreachable', local: { supported: true, mode: 'service', running: 'stopped', prompt_base_url: null } }));
  assert.deepEqual(recovery.getNativeRecoveryState(), { code: 'target_unreachable' });
  bridge.emit(state('g2', { state: 'incompatible', incompatible: { side: 'app', host_contract: { current: 2, min_client: 2 }, client_contract: 1, min_daemon: 1 } }));
  assert.deepEqual(recovery.getNativeRecoveryState(), { code: 'incompatible', side: 'app' });
  bridge.emit(state('g2'));
  assert.equal(recovery.getNativeRecoveryState(), null);
  bridge.emit({ nonsense: true }); // invalid payload is ignored
  assert.equal(target.currentGeneration(), 'g2');
});

// --- query client --------------------------------------------------------------------------
test('query client: desktop hashes include the generation; a change cancels, moves observers, removes old queries', async () => {
  const bridge = desktop(state('g1'));
  await target.captureTarget();
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false, staleTime: 30_000, gcTime: Infinity } } }); // no gc timer keeping node --test alive
  attachNativeQueryClient(qc);
  const fetchedWith = [];
  const observer = new QueryObserver(qc, {
    queryKey: ['orgs'],
    queryFn: () => { fetchedWith.push(target.currentGeneration()); return Promise.resolve(target.currentGeneration()); },
  });
  const unsubscribe = observer.subscribe(() => {});
  await new Promise((r) => setTimeout(r, 0));
  assert.equal(qc.getQueryData(['orgs']), 'g1');
  qc.setQueryData(['other'], 'old-target-data');
  const oldHash = qc.getQueryCache().find({ queryKey: ['orgs'] }).queryHash;

  bridge.emit(state('g2'));
  await new Promise((r) => setTimeout(r, 0));
  const all = qc.getQueryCache().getAll();
  assert.equal(all.length, 1, 'old-generation queries removed');
  assert.notEqual(all[0].queryHash, oldHash);
  assert.ok(all[0].queryHash.includes('g2'));
  assert.equal(qc.getQueryData(['other']), undefined);
  assert.deepEqual(fetchedWith, ['g1', 'g2'], 'mounted observer refetched once under g2');
  assert.equal(observer.getCurrentResult().data, 'g2');
  unsubscribe();
});

test('query client: browser mode is untouched', () => {
  const qc = new QueryClient();
  attachNativeQueryClient(qc);
  assert.equal(qc.getDefaultOptions().queries?.queryKeyHashFn, undefined);
});

// --- SSE -------------------------------------------------------------------------------------
function sseOpen() {
  return (init) => new Response(new ReadableStream({
    start(controller) {
      init.signal?.addEventListener('abort', () => { try { controller.error(new DOMException('Aborted', 'AbortError')); } catch { /* closed */ } });
    },
  }), { status: 200, headers: { 'Content-Type': 'text/event-stream' } });
}

test('SSE: stamps the generation; a change aborts the old stream and resubscribes under the new one', async () => {
  const bridge = desktop(state('g1'));
  queue.push(sseOpen());
  queue.push(sseOpen());
  const ctl = new AbortController();
  const done = subscribeSSE('/orgs/demo/threads/events', { signal: ctl.signal, onMessage() {} });
  await new Promise((r) => setTimeout(r, 20));
  assert.deepEqual(calls.map((c) => c.target), ['g1']);
  bridge.emit(state('g2'));
  await new Promise((r) => setTimeout(r, 20));
  assert.deepEqual(calls.map((c) => c.target), ['g1', 'g2']);
  assert.equal(calls[0].signal.aborted, true, 'old-generation stream aborted');
  ctl.abort();
  await done;
});

test('SSE: 409 target_changed waits for the new generation before resubscribing', async () => {
  const bridge = desktop(state('g1'));
  queue.push(json(409, { detail: { code: 'target_changed' } }));
  queue.push(sseOpen());
  const ctl = new AbortController();
  const done = subscribeSSE('/x', { signal: ctl.signal, onMessage() {} });
  await new Promise((r) => setTimeout(r, 1200)); // past fetch-event-source's 1s retry interval
  assert.equal(calls.length, 1, 'no resubscribe while the generation is unchanged');
  bridge.emit(state('g3'));
  await new Promise((r) => setTimeout(r, 20));
  assert.deepEqual(calls.map((c) => c.target), ['g1', 'g3']);
  ctl.abort();
  await done;
});

test('SSE W-1: 503 busy at open retries (transient), never page-blocking', async () => {
  queue.push(json(503, { detail: { code: 'busy' } }));
  queue.push(sseOpen());
  const ctl = new AbortController();
  const done = subscribeSSE('/x', { signal: ctl.signal, onMessage() {} });
  await new Promise((r) => setTimeout(r, 700));
  assert.equal(calls.length, 2);
  assert.equal(recovery.getNativeRecoveryState(), null);
  assert.equal(notices.getNativeNotice(), null, 'cleared by the successful open');
  ctl.abort();
  await done;
});

test('ApiError subclasses keep existing per-feature handling (status/code)', () => {
  for (const E of [StaleTargetError, NotSentError]) {
    const e = new E();
    assert.ok(e instanceof ApiError && e.status === 409 && e.code === 'target_changed');
  }
});
