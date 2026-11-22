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

test('C1: a 499 ResultUnknownError save is classified unknown by the owner registry (never discardable)', async () => {
  const { classifySaveError } = await import('../src/lib/owners/ownerRegistry.ts');
  desktop(state('g1'));
  queue.push(json(499, { detail: { code: 'cancelled' } }));
  const err = await request('/x', { method: 'PUT', body: {} }).catch((e) => e);
  assert.ok(err instanceof ResultUnknownError);
  assert.equal(classifySaveError(err), 'unknown');
  assert.equal(classifySaveError(new ResultUnknownError()), 'unknown');
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

// --- TASK-18178 review round 1 (TASK-18175 F1-F5) ------------------------------------------
const tick = (ms = 0) => new Promise((r) => setTimeout(r, ms));
function bootstrapThen(effect, token = 'tok-2') {
  return () => {
    effect();
    return json(200, { token })();
  };
}

// F1 — one generation per logical operation, kept across the bearer re-send.
test('F1 request: a mutation 401 under g1, switch to g2 during re-auth -> Not sent, never written under g2', async () => {
  const bridge = desktop(state('g1'));
  await target.captureTarget();
  queue.push(json(401, { detail: 'expired' }));
  queue.push(bootstrapThen(() => bridge.emit(state('g2'))));
  const err = await request('/orgs/demo/tasks/TASK-1/cancel', { method: 'POST' }).catch((e) => e);
  assert.ok(err instanceof NotSentError, String(err));
  assert.deepEqual(calls.map((c) => [new URL(c.url, 'http://h').pathname, c.target]), [
    ['/api/v1/orgs/demo/tasks/TASK-1/cancel', 'g1'],
    ['/api/v1/auth/bootstrap', null],
  ]);
  assert.equal(calls.filter((c) => c.target === 'g2').length, 0, 'no write under the second target');
  assert.equal(notices.getNativeNotice(), 'not_sent');
});

test('F1 request: a switch during the FIRST bootstrap (pinned before it) never sends the mutation', async () => {
  const bridge = desktop(state('g1'));
  await target.captureTarget();
  sessionStorage.removeItem('happyranch.token');
  queue.push(bootstrapThen(() => bridge.emit(state('g2'))));
  const err = await request('/x', { method: 'PATCH', body: { a: 1 } }).catch((e) => e);
  assert.ok(err instanceof NotSentError, String(err));
  assert.deepEqual(PATHS(), ['/api/v1/auth/bootstrap']);
});

test('F1 request: a GET 401 then switch refetches once under the new generation', async () => {
  const bridge = desktop(state('g1'));
  await target.captureTarget();
  queue.push(json(401, { detail: 'expired' }));
  queue.push(bootstrapThen(() => bridge.emit(state('g2'))));
  queue.push(json(200, { orgs: [] }));
  assert.deepEqual(await request('/orgs'), { orgs: [] });
  assert.deepEqual(calls.map((c) => c.target), ['g1', null, 'g2']);
});

test('F1 request: unchanged generation keeps the existing single 401 re-send under the same pin', async () => {
  desktop(state('g1'));
  await target.captureTarget();
  queue.push(json(401, { detail: 'expired' }));
  queue.push(json(200, { token: 'tok-2' }));
  queue.push(json(200, { ok: 1 }));
  assert.deepEqual(await request('/x', { method: 'POST', body: {} }), { ok: 1 });
  assert.deepEqual(calls.map((c) => c.target), ['g1', null, 'g1']);
});

test('F1 browser: 401 re-send behavior unchanged (two sends, no header)', async () => {
  queue.push(json(401, { detail: 'expired' }));
  queue.push(json(200, { token: 'tok-2' }));
  queue.push(json(200, { ok: 1 }));
  assert.deepEqual(await request('/x', { method: 'POST', body: {} }), { ok: 1 });
  assert.deepEqual(calls.map((c) => c.target), [null, null, null]);
  assert.equal(PATHS()[1], '/api/v1/auth/bootstrap');
});

test('F1 artifacts: upload and delete 401 then switch during re-auth -> Not sent, no g2 write', async () => {
  for (const run of [
    () => artifacts.uploadArtifact('demo', { file: new File(['x'], 'a.txt'), agent: 'founder' }),
    () => artifacts.deleteArtifact('demo', 'a.txt'),
  ]) {
    calls = [];
    target.__setNativeBridgeForTests(null);
    notices.__resetNativeNoticeForTests();
    sessionStorage.setItem('happyranch.token', 'tok-1');
    const bridge = desktop(state('g1'));
    await target.captureTarget();
    queue.push(json(401, { detail: 'expired' }));
    queue.push(bootstrapThen(() => bridge.emit(state('g2'))));
    const err = await run().catch((e) => e);
    assert.ok(err instanceof NotSentError, String(err));
    assert.deepEqual(calls.map((c) => c.target), ['g1', null]);
    assert.equal(notices.getNativeNotice(), 'not_sent');
  }
});

test('F1 artifacts: list (GET) 401 then switch refetches once under g2', async () => {
  const bridge = desktop(state('g1'));
  await target.captureTarget();
  queue.push(json(401, { detail: 'expired' }));
  queue.push(bootstrapThen(() => bridge.emit(state('g2'))));
  queue.push(json(200, { artifacts: [] }));
  assert.deepEqual(await artifacts.listArtifacts('demo'), { artifacts: [] });
  assert.deepEqual(calls.map((c) => c.target), ['g1', null, 'g2']);
});

// F4 — an old error body never publishes for the new target.
function deferredBody(status) {
  let controller;
  const respond = () => new Response(new ReadableStream({ start(c) { controller = c; } }), {
    status, headers: { 'Content-Type': 'application/json' },
  });
  const deliver = (body) => {
    controller.enqueue(new TextEncoder().encode(JSON.stringify(body)));
    controller.close();
  };
  return { respond, deliver };
}

test('F4 GET: generation changes before a deferred 503 busy body -> StaleTargetError, no notice, single send', async () => {
  const bridge = desktop(state('g1'));
  await target.captureTarget();
  const body = deferredBody(503);
  queue.push(body.respond);
  const pending = request('/orgs');
  await tick(10);
  bridge.emit(state('g2'));
  body.deliver({ detail: { code: 'busy' } });
  const err = await pending.catch((e) => e);
  assert.ok(err instanceof StaleTargetError, String(err));
  assert.equal(notices.getNativeNotice(), null);
  assert.equal(recovery.getNativeRecoveryState(), null);
  assert.equal(calls.length, 1);
});

test('F4 SSE: generation changes before a deferred 503 busy open body -> no notice, resubscribes under g2', async () => {
  const bridge = desktop(state('g1'));
  const body = deferredBody(503);
  queue.push(body.respond);
  queue.push(sseOpen());
  const published = [];
  notices.subscribeNativeNotice(() => published.push(notices.getNativeNotice()));
  const ctl = new AbortController();
  const done = subscribeSSE('/x', { signal: ctl.signal, onMessage() {} });
  await tick(10);
  bridge.emit(state('g2'));
  body.deliver({ detail: { code: 'busy' } });
  await tick(30);
  assert.deepEqual(published.filter((n) => n === 'busy'), [], 'old body never published busy (not even transiently)');
  assert.deepEqual(calls.map((c) => c.target), ['g1', 'g2']);
  ctl.abort();
  await done;
});

// F5 — native mutation transport loss is Result unknown, never a raw failure or replay.
test('F5: a rejected native mutation fetch WITHOUT a generation event -> ResultUnknownError + notice, single send', async () => {
  desktop(state('g1'));
  await target.captureTarget();
  queue.push(() => { throw new TypeError('synthetic transfer failure after forwarding'); });
  const err = await request('/orgs/demo/tasks', { method: 'POST', body: { brief: 'synthetic' } }).catch((e) => e);
  assert.ok(err instanceof ResultUnknownError, String(err));
  assert.equal(target.currentGeneration(), 'g1', 'no switch happened');
  assert.equal(calls.length, 1, 'never replayed');
  assert.equal(notices.getNativeNotice(), 'result_unknown');
});

test('F5: pre-forward refusals stay distinguishable; GET transport errors, caller aborts and browser unchanged', async () => {
  desktop(state('g1'));
  await target.captureTarget();
  queue.push(json(409, { detail: { code: 'target_changed' } }));
  assert.ok((await request('/x', { method: 'POST', body: {} }).catch((e) => e)) instanceof NotSentError);
  queue.push(json(429, { detail: { code: 'too_many_requests' } }));
  assert.ok((await request('/x', { method: 'POST', body: {} }).catch((e) => e)) instanceof ThrottledError);
  queue.push(json(503, { detail: { code: 'busy' } }));
  const busy = await request('/x', { method: 'POST', body: {} }).catch((e) => e);
  assert.ok(busy instanceof NativeRecoveryError && !(busy instanceof NotSentError) && busy.code === 'busy');
  queue.push(() => { throw new TypeError('read transport'); });
  assert.equal((await request('/orgs').catch((e) => e)).constructor, TypeError, 'GET keeps its raw error');
  const ctl = new AbortController();
  queue.push(() => { ctl.abort(); throw new DOMException('Aborted', 'AbortError'); });
  const aborted = await request('/x', { method: 'POST', body: {}, signal: ctl.signal }).catch((e) => e);
  assert.equal(aborted.name, 'AbortError', "the caller's own abort keeps its error");
  target.__setNativeBridgeForTests(null);
  globalThis.location = BROWSER_LOCATION;
  queue.push(() => { throw new TypeError('browser transport'); });
  assert.equal((await request('/x', { method: 'POST', body: {} }).catch((e) => e)).constructor, TypeError, 'browser unchanged');
});

// F3 — failed reads recover when busy/throttled clears; mutations never auto-retry.
function orgsClient() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false, refetchOnWindowFocus: false, staleTime: 30_000, gcTime: Infinity } } });
  const detach = attachNativeQueryClient(qc);
  const observer = new QueryObserver(qc, { queryKey: ['orgs'], queryFn: () => request('/orgs') });
  const unsubscribe = observer.subscribe(() => {});
  return { qc, observer, close: () => { unsubscribe(); detach(); qc.clear(); } };
}

test('F3: bootstrap busy -> native ready -> org data loaded (the failed read is refetched once)', async () => {
  const bridge = desktop(state('g1', { state: 'busy' }));
  await target.captureTarget();
  sessionStorage.removeItem('happyranch.token');
  queue.push(json(503, { detail: { code: 'busy' } })); // bootstrap
  const { observer, close } = orgsClient();
  await tick(10);
  assert.equal(observer.getCurrentResult().status, 'error');
  queue.push(json(200, { token: 'tok-3' }));
  queue.push(json(200, { orgs: [{ slug: 'demo' }] }));
  bridge.emit(state('g1')); // same generation, ready
  await tick(20);
  assert.deepEqual(observer.getCurrentResult().data, { orgs: [{ slug: 'demo' }] });
  assert.deepEqual(PATHS(), ['/api/v1/auth/bootstrap', '/api/v1/auth/bootstrap', '/api/v1/orgs']);
  close();
});

test('F3: exhausted GET busy backoff -> recovery (ready) -> org data loaded', async () => {
  const bridge = desktop(state('g1'));
  await target.captureTarget();
  for (let i = 0; i < 4; i += 1) queue.push(json(503, { detail: { code: 'busy' } }));
  const { observer, close } = orgsClient();
  await tick(3650);
  assert.equal(calls.length, 4);
  assert.equal(observer.getCurrentResult().status, 'error');
  queue.push(json(200, { orgs: [{ slug: 'demo' }] }));
  bridge.emit(state('g1'));
  await tick(20);
  assert.equal(calls.length, 5);
  assert.deepEqual(observer.getCurrentResult().data, { orgs: [{ slug: 'demo' }] });
  close();
});

test('F3: a later forwarded success also recovers; non-transient failures and mutations are not retried', async () => {
  desktop(state('g1'));
  await target.captureTarget();
  queue.push(json(429, { detail: { code: 'too_many_requests' } }));
  const err = await request('/x', { method: 'POST', body: {} }).catch((e) => e);
  assert.ok(err instanceof ThrottledError);
  for (let i = 0; i < 4; i += 1) queue.push(json(429, { detail: { code: 'too_many_requests' } }));
  const { qc, observer, close } = orgsClient();
  await tick(3650);
  assert.equal(observer.getCurrentResult().status, 'error');
  queue.push(json(500, { detail: 'boom' }));
  const other = new QueryObserver(qc, { queryKey: ['plain-failure'], queryFn: () => request('/broken') });
  const offOther = other.subscribe(() => {});
  await tick(10);
  assert.equal(other.getCurrentResult().status, 'error');
  const before = calls.length;
  queue.push(json(200, { ok: 1 }));
  queue.push(json(200, { orgs: [] }));
  await request('/ping'); // a later forwarded success
  await tick(20);
  assert.equal(calls.length, before + 2, 'ping + one org refetch; no mutation replay, no plain-failure refetch');
  assert.deepEqual(observer.getCurrentResult().data, { orgs: [] });
  assert.equal(other.getCurrentResult().status, 'error');
  offOther();
  close();
});

// F2 — editor custody: a loaded Settings editor keeps its data object (and so
// its draft) across a switch, with no new-target fetch, and no change is sent
// to the new target while it is held.
test('F2: custody observer keeps the same data object across the switch and later renders; no reread', async () => {
  const bridge = desktop(state('g1'));
  await target.captureTarget();
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false, staleTime: 30_000, gcTime: Infinity } } });
  const detach = attachNativeQueryClient(qc, { custody: [['settings']] });
  const fetchedWith = [];
  const options = {
    queryKey: ['settings', 'demo'],
    queryFn: () => { fetchedWith.push(target.currentGeneration()); return Promise.resolve({ org: { name: `org-${target.currentGeneration()}` } }); },
  };
  const observer = new QueryObserver(qc, options);
  const unsubscribe = observer.subscribe(() => {});
  await tick(0);
  const loaded = observer.getCurrentResult().data;
  assert.deepEqual(loaded, { org: { name: 'org-g1' } });
  // Model of the Settings editor: mounted while data exists; its local draft
  // resets only when the `org` object identity changes (OrganizationSection).
  const editor = { org: loaded.org, draft: 'edited name' };
  const render = () => {
    // Exactly what useQuery does on every render: re-default the options
    // (new-generation hash) and ask the observer for its optimistic result.
    const defaulted = qc.defaultQueryOptions(options);
    const result = observer.getOptimisticResult(defaulted);
    observer.setOptions(defaulted);
    assert.ok(result.data, 'data gate stays open: the editor stays mounted');
    if (result.data.org !== editor.org) editor.draft = null; // external reset
    editor.org = result.data.org;
  };

  bridge.emit(state('g2'));
  await tick(0);
  render();
  await qc.invalidateQueries({ queryKey: ['settings'] });
  await observer.refetch();
  await tick(0);
  render();
  render();
  assert.equal(observer.getCurrentResult().data, loaded, 'same object: no reread, no reset');
  assert.equal(editor.draft, 'edited name', 'draft survives');
  assert.deepEqual(fetchedWith, ['g1'], 'no new-target fetch into the held editor');
  assert.equal(qc.getQueryData(['settings', 'demo']), undefined, 'the new target has no entry from old data');

  // While held, no change is sent to the new target.
  const err = await request('/orgs/demo/settings', { method: 'PATCH', body: { name: 'edited name' } }).catch((e) => e);
  assert.ok(err instanceof NotSentError, String(err));
  assert.equal(calls.length, 0, 'nothing sent');
  assert.equal(notices.getNativeNotice(), 'not_sent');

  // The owner unmounts and a new observer mounts: hold released, new target
  // read. (Same-observer re-keying is covered by the R2-2 test below.)
  unsubscribe();
  assert.equal(target.targetCustodyHeld(), false);
  assert.equal(qc.getQueryCache().getAll().length, 0, 'older-target entry removed');
  const fresh = new QueryObserver(qc, options);
  const offFresh = fresh.subscribe(() => {});
  await tick(0);
  assert.deepEqual(fresh.getCurrentResult().data, { org: { name: 'org-g2' } });
  assert.deepEqual(fetchedWith, ['g1', 'g2']);
  queue.push(json(200, { ok: 1 }));
  await request('/orgs/demo/settings', { method: 'PATCH', body: {} });
  assert.deepEqual(calls.map((c) => c.target), ['g2']);
  offFresh();
  detach();
  qc.clear();
});

test('F2: non-custody and not-yet-loaded custody observers still move; no hold is taken', async () => {
  const bridge = desktop(state('g1'));
  await target.captureTarget();
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false, staleTime: 30_000, gcTime: Infinity } } });
  const detach = attachNativeQueryClient(qc, { custody: [['settings']] });
  const fetchedWith = [];
  const tasksObserver = new QueryObserver(qc, { queryKey: ['tasks'], queryFn: () => { fetchedWith.push(['tasks', target.currentGeneration()]); return Promise.resolve(target.currentGeneration()); } });
  let releaseSettings;
  const pendingSettings = new QueryObserver(qc, {
    queryKey: ['settings', 'demo'],
    queryFn: () => {
      fetchedWith.push(['settings', target.currentGeneration()]);
      if (target.currentGeneration() === 'g1') return new Promise((r) => { releaseSettings = r; });
      return Promise.resolve('settings-g2');
    },
  });
  const off1 = tasksObserver.subscribe(() => {});
  const off2 = pendingSettings.subscribe(() => {});
  await tick(0);
  bridge.emit(state('g2'));
  await tick(0);
  releaseSettings?.('settings-g1');
  await tick(0);
  assert.equal(tasksObserver.getCurrentResult().data, 'g2');
  assert.equal(pendingSettings.getCurrentResult().data, 'settings-g2', 'an unloaded custody read is not held');
  assert.equal(target.targetCustodyHeld(), false);
  off1();
  off2();
  detach();
  qc.clear();
});

test('F4 mutation: switch before a deferred pre-forward 409 body -> Not sent (provably not forwarded), single send', async () => {
  const bridge = desktop(state('g1'));
  await target.captureTarget();
  const body = deferredBody(409);
  queue.push(body.respond);
  const pending = request('/x', { method: 'POST', body: {} });
  await tick(10);
  bridge.emit(state('g2'));
  body.deliver({ detail: { code: 'target_changed' } });
  const err = await pending.catch((e) => e);
  assert.ok(err instanceof NotSentError, String(err));
  assert.equal(notices.getNativeNotice(), 'not_sent');
  assert.equal(calls.length, 1);
});

test('F4 mutation: switch before a deferred ordinary error body -> Result unknown, single send', async () => {
  const bridge = desktop(state('g1'));
  await target.captureTarget();
  const body = deferredBody(409);
  queue.push(body.respond);
  const pending = request('/x', { method: 'POST', body: {} });
  await tick(10);
  bridge.emit(state('g2'));
  body.deliver({ detail: { code: 'conflict' } });
  const err = await pending.catch((e) => e);
  assert.ok(err instanceof ResultUnknownError, String(err));
  assert.equal(notices.getNativeNotice(), 'result_unknown');
  assert.equal(calls.length, 1);
});

// R2-1 — a recovery signal that fires while a transient read is still pending
// is not lost: the read is refetched once when it settles as an error.
test('R2-1: bootstrap busy body pending while native turns ready -> refetched once, org data loaded', async () => {
  const bridge = desktop(state('g1', { state: 'busy' }));
  await target.captureTarget();
  sessionStorage.removeItem('happyranch.token');
  const body = deferredBody(503);
  queue.push(body.respond); // bootstrap, body still pending
  const { observer, close } = orgsClient();
  await tick(10);
  assert.equal(observer.getCurrentResult().fetchStatus, 'fetching');
  queue.push(json(200, { token: 'tok-3' }));
  queue.push(json(200, { orgs: [{ slug: 'demo' }] }));
  bridge.emit(state('g1')); // ready, same generation, while the read is pending
  body.deliver({ detail: { code: 'busy' } });
  await tick(30);
  assert.deepEqual(observer.getCurrentResult().data, { orgs: [{ slug: 'demo' }] });
  assert.deepEqual(PATHS(), ['/api/v1/auth/bootstrap', '/api/v1/auth/bootstrap', '/api/v1/orgs']);
  assert.equal(notices.getNativeNotice(), null, 'the later success clears busy (W-2a)');
  close();
});

test('R2-1: final GET backoff attempt pending while native turns ready -> refetched once, org data loaded', async () => {
  const bridge = desktop(state('g1', { state: 'busy' }));
  await target.captureTarget();
  for (let i = 0; i < 3; i += 1) queue.push(json(503, { detail: { code: 'busy' } }));
  const body = deferredBody(503);
  queue.push(body.respond); // fourth (final) attempt, body still pending
  const { observer, close } = orgsClient();
  for (let waited = 0; calls.length < 4 && waited < 5000; waited += 50) await tick(50);
  assert.equal(calls.length, 4);
  assert.equal(observer.getCurrentResult().fetchStatus, 'fetching');
  queue.push(json(200, { orgs: [{ slug: 'demo' }] }));
  bridge.emit(state('g1'));
  body.deliver({ detail: { code: 'busy' } });
  await tick(30);
  assert.equal(calls.length, 5, 'exactly one recovery read');
  assert.deepEqual(observer.getCurrentResult().data, { orgs: [{ slug: 'demo' }] });
  close();
});

test('R2-1 controls: a pending mutation is never replayed; a transient failure with no signal is not refetched', async () => {
  const bridge = desktop(state('g1'));
  await target.captureTarget();
  const { observer, close } = orgsClient();
  queue.push(json(200, { orgs: [] }));
  await tick(10);
  assert.equal(observer.getCurrentResult().status, 'success');
  const body = deferredBody(503);
  queue.push(body.respond);
  const pending = request('/x', { method: 'POST', body: {} });
  await tick(10);
  bridge.emit(state('g1')); // ready while the change is pending
  body.deliver({ detail: { code: 'busy' } });
  const err = await pending.catch((e) => e);
  assert.ok(err instanceof NativeRecoveryError && err.recovery.code === 'busy', String(err));
  await tick(30);
  assert.deepEqual(PATHS(), ['/api/v1/orgs', '/api/v1/x'], 'no mutation replay, no extra read');
  // A transient read failure that settles without any signal since its start
  // stays failed (no loop).
  const qc2 = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: Infinity } } });
  const detach2 = attachNativeQueryClient(qc2);
  let reads = 0;
  // A transient (throttled) failure, without the GET backoff delay.
  const throttled = new QueryObserver(qc2, { queryKey: ['t'], queryFn: () => { reads += 1; return Promise.reject(new ThrottledError()); } });
  const offT = throttled.subscribe(() => {});
  await tick(30);
  assert.equal(throttled.getCurrentResult().status, 'error');
  assert.equal(reads, 1, 'refetched zero times');
  offT();
  detach2();
  qc2.clear();
  close();
});

// R2-2 — custody is scoped to the original logical key: the SAME observer
// re-keyed by its owner (Settings following an org change) releases the pin.
test('R2-2: same observer re-keyed after a switch releases custody and reads the new key under the new generation', async () => {
  const bridge = desktop(state('g1'));
  await target.captureTarget();
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false, staleTime: 30_000, gcTime: Infinity } } });
  const detach = attachNativeQueryClient(qc, { custody: [['settings']] });
  const fetched = [];
  const options = (slug) => ({
    queryKey: ['settings', slug],
    queryFn: () => { fetched.push([slug, target.currentGeneration()]); return Promise.resolve({ org: { name: slug } }); },
  });
  const observer = new QueryObserver(qc, options('org-a'));
  const unsubscribe = observer.subscribe(() => {});
  await tick(0);
  const loaded = observer.getCurrentResult().data;
  const render = (slug) => {
    // What useQuery does on every render with the owner's current options.
    const defaulted = qc.defaultQueryOptions(options(slug));
    const result = observer.getOptimisticResult(defaulted);
    observer.setOptions(defaulted);
    return result;
  };
  bridge.emit(state('g2'));
  await tick(0);
  // Same-key rerenders keep the generation-only draft custody.
  assert.equal(render('org-a').data, loaded);
  assert.equal(render('org-a').data, loaded);
  assert.equal(target.targetCustodyHeld(), true);
  assert.deepEqual(fetched, [['org-a', 'g1']]);
  // Sidebar.onOrgChange: the same observer now receives ['settings', 'org-b'].
  render('org-b');
  await tick(0);
  assert.deepEqual(observer.getCurrentResult().data, { org: { name: 'org-b' } });
  assert.deepEqual(fetched, [['org-a', 'g1'], ['org-b', 'g2']]);
  assert.equal(target.targetCustodyHeld(), false, 'the write fence is released');
  assert.deepEqual(qc.getQueryCache().getAll().map((q) => q.queryKey), [['settings', 'org-b']], 'older-target entry removed');
  // Later rerenders and invalidation behave like any unpinned observer.
  render('org-b');
  await qc.invalidateQueries({ queryKey: ['settings'] });
  await tick(0);
  assert.deepEqual(fetched.at(-1), ['org-b', 'g2']);
  queue.push(json(200, { ok: 1 }));
  await request('/orgs/org-b/settings', { method: 'PATCH', body: {} });
  assert.deepEqual(calls.map((c) => c.target), ['g2'], 'changes go to the current target again');
  unsubscribe();
  detach();
  qc.clear();
});

test('R2-2 control: with two observers on the held entry, one re-keying keeps the hold for the other', async () => {
  const bridge = desktop(state('g1'));
  await target.captureTarget();
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false, staleTime: 30_000, gcTime: Infinity } } });
  const detach = attachNativeQueryClient(qc, { custody: [['settings']] });
  const options = (slug) => ({ queryKey: ['settings', slug], queryFn: () => Promise.resolve({ slug, g: target.currentGeneration() }) });
  const a = new QueryObserver(qc, options('org-a'));
  const b = new QueryObserver(qc, options('org-a'));
  const offA = a.subscribe(() => {});
  const offB = b.subscribe(() => {});
  await tick(0);
  const loaded = b.getCurrentResult().data;
  bridge.emit(state('g2'));
  await tick(0);
  a.setOptions(qc.defaultQueryOptions(options('org-b')));
  await tick(0);
  assert.deepEqual(a.getCurrentResult().data, { slug: 'org-b', g: 'g2' });
  assert.equal(b.getCurrentResult().data, loaded, 'the other editor keeps its data');
  assert.equal(target.targetCustodyHeld(), true);
  offB();
  assert.equal(target.targetCustodyHeld(), false);
  offA();
  detach();
  qc.clear();
});

// R2-3 — bootstrap's own error body is fenced against the generation captured
// at bootstrap start (no header, but no stale side effects either).
test('R2-3: deferred bootstrap no_target body after a switch to g2/ready -> no recovery, no notice, stale (read)', async () => {
  const bridge = desktop(state('g1', { state: 'no_target', target: null }));
  await target.captureTarget();
  assert.equal(recovery.getNativeRecoveryState()?.code, 'no_target');
  sessionStorage.removeItem('happyranch.token');
  const body = deferredBody(503);
  queue.push(body.respond);
  const read = request('/orgs').catch((e) => e);
  const write = request('/x', { method: 'POST', body: {} }).catch((e) => e); // shares the in-flight bootstrap
  await tick(10);
  bridge.emit(state('g2'));
  assert.equal(recovery.getNativeRecoveryState(), null, 'ready cleared it');
  body.deliver({ detail: { code: 'no_target' } });
  const readErr = await read;
  const writeErr = await write;
  // Behavior first (the old body published nothing), then classification.
  assert.equal(recovery.getNativeRecoveryState(), null, 'the old body published nothing');
  assert.ok(readErr instanceof StaleTargetError, String(readErr));
  assert.ok(writeErr instanceof NotSentError, String(writeErr));
  assert.equal(notices.getNativeNotice(), 'not_sent', 'only the change outcome (nothing was sent)');
  assert.equal(target.getAppStatus().generation, 'g2');
  assert.equal(target.getAppStatus().state, 'ready');
  assert.deepEqual(PATHS(), ['/api/v1/auth/bootstrap'], 'single send; the change was never sent');
});

test('R2-3: deferred bootstrap busy body after a switch -> no busy notice; same-generation busy still notifies', async () => {
  const bridge = desktop(state('g1'));
  await target.captureTarget();
  sessionStorage.removeItem('happyranch.token');
  let body = deferredBody(503);
  queue.push(body.respond);
  const read = request('/orgs').catch((e) => e);
  await tick(10);
  bridge.emit(state('g2'));
  body.deliver({ detail: { code: 'busy' } });
  const readErr = await read;
  assert.equal(notices.getNativeNotice(), null, 'no busy notice for g2');
  assert.ok(readErr instanceof StaleTargetError, String(readErr));
  assert.equal(recovery.getNativeRecoveryState(), null);
  assert.equal(target.getAppStatus().state, 'ready');
  // Control: same generation keeps the W-1 busy notice and BootstrapError.
  body = deferredBody(503);
  queue.push(body.respond);
  const again = request('/orgs').catch((e) => e);
  await tick(10);
  body.deliver({ detail: { code: 'busy' } });
  assert.equal((await again).name, 'BootstrapError');
  assert.equal(notices.getNativeNotice(), 'busy');
});

test('R2-3 browser control: bootstrap failure behavior unchanged (BootstrapError, recovery parsed as before)', async () => {
  sessionStorage.removeItem('happyranch.token');
  queue.push(json(401, { detail: 'nope' }));
  const err = await request('/orgs').catch((e) => e);
  assert.equal(err.name, 'BootstrapError');
  assert.equal(err.status, 401);
  assert.deepEqual(PATHS(), ['/api/v1/auth/bootstrap']);
});

// R3-1 — the current generation never inherits an older generation's pending
// bootstrap. Mirrors reviewer probe cross-generation-bootstrap.mjs (TASK-18189).
test('R3-1: g1 no_target bootstrap body pending, switch to g2/ready -> mounted orgs query bootstraps afresh and loads', async () => {
  const bridge = desktop(state('g1', { state: 'no_target', target: null }));
  await target.captureTarget();
  sessionStorage.removeItem('happyranch.token');
  const body = deferredBody(503);
  queue.push(body.respond); // g1 bootstrap, error body still pending
  const { qc, observer, close } = orgsClient();
  await tick(10);
  const oldWrite = request('/x', { method: 'POST', body: {} }).catch((e) => e); // shares the g1 bootstrap
  await tick(10);
  queue.push(json(200, { token: 'tok-g2' })); // the g2 query's own bootstrap
  queue.push(json(200, { orgs: [{ slug: 'demo' }] }));
  bridge.emit(state('g2'));
  await tick(10);
  body.deliver({ detail: { code: 'no_target' } });
  await tick(50);
  const result = observer.getCurrentResult();
  assert.deepEqual(result.data, { orgs: [{ slug: 'demo' }] }, `current target org data loaded (${result.status} ${result.error?.name})`);
  assert.deepEqual(qc.getQueryCache().getAll().map((q) => [q.queryHash, q.state.status]), [
    ['[{"target":"g2"},"orgs"]', 'success'],
  ]);
  // The old generation's consumer stays stale/not sent and is never replayed.
  assert.ok((await oldWrite) instanceof NotSentError);
  assert.deepEqual(calls.map((c) => [new URL(c.url, 'http://h').pathname, c.method, c.target]), [
    ['/api/v1/auth/bootstrap', 'GET', null],
    ['/api/v1/auth/bootstrap', 'GET', null],
    ['/api/v1/orgs', 'GET', 'g2'],
  ]);
  assert.equal(recovery.getNativeRecoveryState(), null, 'the old body published nothing');
  close();
});

// R3-2 — explicit-token callers pin the save's generation before preparing the
// bearer. Mirrors reviewer probe explicit-token-pin.mjs (TASK-18189).
const sequential = await import('../src/lib/api/workflowSequential.ts');
const settingsApi = await import('../src/lib/api/settings.ts');
const SEQ_COMMAND = {
  format: 'workflow-sequential-settings-put@1', operation_key: 's5-w1-test-key', expected_generation: 1,
  definition: { identity: 'review/synthetic', version: 1, definition_digest: 'a'.repeat(64), source_digest: 'b'.repeat(64),
    compiler: 'workflow-sequential-compiler@1', validator: 'workflow-sequential-validator@1',
    interpreter: 'workflow-sequential-interpreter@1', capabilities: 'workflow-sequential-capabilities@1' },
  bindings: [{ role: 'author', principal: 'agent:synthetic' }],
  limits: { content_returns: 1, max_content_epochs: 1, max_visits: 10, max_events: 100 },
};

for (const [name, save] of [
  ['Workflow Setup putSequentialSettings', () => sequential.putSequentialSettings('demo', SEQ_COMMAND)],
  ['Coding settings putCodingSettings', () => settingsApi.putCodingSettings('demo', { org_slug: 'demo' })],
]) {
  test(`R3-2: ${name} authored under g1, bootstrap resolves after switch to g2 -> Not sent, no PUT`, async () => {
    const bridge = desktop(state('g1'));
    await target.captureTarget();
    sessionStorage.removeItem('happyranch.token');
    let release;
    queue.push(() => new Promise((r) => { release = () => r(json(200, { token: 'tok-2' })()); }));
    const pending = save().catch((e) => e);
    await tick(10);
    bridge.emit(state('g2'));
    release();
    const err = await pending;
    assert.deepEqual(calls.map((c) => [new URL(c.url, 'http://h').pathname, c.method, c.target]), [['/api/v1/auth/bootstrap', 'GET', null]], 'no PUT to either target');
    assert.ok(err instanceof NotSentError, String(err));
    assert.equal(notices.getNativeNotice(), 'not_sent');
  });
}

test('R3-2 control: explicit-token save keeps single send on 401 (no re-bootstrap, no replay) under an unchanged generation', async () => {
  desktop(state('g1'));
  await target.captureTarget();
  queue.push(json(401, { detail: 'expired' }));
  const err = await sequential.putSequentialSettings('demo', SEQ_COMMAND).catch((e) => e);
  assert.equal(err.status, 401);
  assert.deepEqual(calls.map((c) => [new URL(c.url, 'http://h').pathname, c.method, c.target]), [
    ['/api/v1/orgs/demo/workflows/sequential/settings', 'PUT', 'g1'],
  ]);
  assert.equal(sessionStorage.getItem('happyranch.token'), 'tok-1', 'explicit-token path does not clear the bearer');
});

// THR-387 S8-2b (review F1) — native's exact pre-forward
// `403 {"detail":{"code":"remote_read_only"}}` survives generation fencing
// when app.stateChanged (native committing the remote target) lands before
// the response headers or during the body. It is the read-only refusal (not
// sent, draft kept), never 499 cancelled / Result unknown, and never replayed.
// Every other answer across the same orderings stays Result unknown.
const REMOTE_STATE = () => state('g2', {
  target: { kind: 'remote', id: 'saved-studio', name: 'Studio Host' },
  local: { supported: true, mode: 'service', running: 'stopped', prompt_base_url: null },
});
const READ_ONLY_BODY = { detail: { code: 'remote_read_only' } };

/** One mutation through request(); the status event fires at `when`. */
async function refusalAcrossSwitch(when, body, method = 'PUT') {
  const bridge = desktop(state('g1'));
  await target.captureTarget();
  if (when === 'before-headers') {
    queue.push(() => {
      bridge.emit(REMOTE_STATE());
      return json(403, body)();
    });
    return request('/orgs/demo/settings/org', { method, body: method === 'GET' ? undefined : { session_timeout: 5400 } }).catch((e) => e);
  }
  const deferred = deferredBody(403);
  queue.push(deferred.respond);
  const pending = request('/orgs/demo/settings/org', { method, body: method === 'GET' ? undefined : { session_timeout: 5400 } }).catch((e) => e);
  await tick(10);
  bridge.emit(REMOTE_STATE());
  deferred.deliver(body);
  return pending;
}

for (const when of ['before-headers', 'during-body']) {
  test(`S8-2b F1: app.stateChanged ${when} of an exact 403 remote_read_only -> read-only refusal (not sent), single send`, async () => {
    const { classifySaveError } = await import('../src/lib/owners/ownerRegistry.ts');
    const err = await refusalAcrossSwitch(when, READ_ONLY_BODY);
    assert.ok(err instanceof ApiError, String(err));
    assert.ok(!(err instanceof ResultUnknownError), `must not be Result unknown: ${err?.status} ${err?.code}`);
    assert.equal(err.status, 403);
    assert.equal(err.code, 'remote_read_only');
    assert.equal(classifySaveError(err), 'failed', 'a confirmed refusal keeps the draft (never unknown / Check result)');
    assert.equal(notices.getNativeNotice(), null, 'no result_unknown (or other) notice');
    assert.equal(recovery.getNativeRecoveryState(), null, 'no recovery state');
    assert.equal(calls.length, 1, 'never replayed');
  });

  test(`S8-2b F1 control: app.stateChanged ${when} of any other 403 / near-miss body -> Result unknown, single send`, async () => {
    for (const body of [
      { detail: { code: 'forbidden' } },
      { detail: { code: 'remote_read_only', reason: 'x' } },
      { detail: 'remote_read_only' },
      { detail: { code: 'remote_read_only' }, extra: 1 },
    ]) {
      calls = [];
      notices.__resetNativeNoticeForTests();
      const err = await refusalAcrossSwitch(when, body);
      assert.ok(err instanceof ResultUnknownError, `${JSON.stringify(body)} -> ${String(err)}`);
      assert.equal(notices.getNativeNotice(), 'result_unknown');
      assert.equal(calls.length, 1);
    }
  });
}

test('S8-2b F1 control: a GET answered 403 remote_read_only after a switch is still dropped as stale', async () => {
  for (const when of ['before-headers', 'during-body']) {
    calls = [];
    const err = await refusalAcrossSwitch(when, READ_ONLY_BODY, 'GET');
    assert.ok(err instanceof StaleTargetError, `${when}: ${String(err)}`);
    assert.equal(calls.length, 1);
  }
});
