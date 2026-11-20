// Focused Node 24 checks for THR-387 S1b native recovery states; no Vitest or
// Product E2E selection. Run from the repository root:
//   node --experimental-transform-types --test web/scripts/native-recovery.test.mjs
// (transform-types is needed because ApiError uses TS parameter properties.)
import { registerHooks } from 'node:module';
import { existsSync } from 'node:fs';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { dirname, resolve as resolvePath } from 'node:path';
import { test, beforeEach } from 'node:test';
import assert from 'node:assert/strict';

const SRC = resolvePath(dirname(fileURLToPath(import.meta.url)), '..', 'src');

// The web source uses extensionless relative imports and the `@/` alias.
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

// Browser globals the modules touch.
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
Object.defineProperty(globalThis, 'location', { configurable: true, value: { protocol: 'http:' } });

const { CLIENT_CONTRACT, MIN_DAEMON, isCompatible } = await import('../src/lib/apiContract.ts');
const recovery = await import('../src/lib/nativeRecovery.ts');
const auth = await import('../src/lib/auth.ts');
const client = await import('../src/lib/api/client.ts');
const artifacts = await import('../src/lib/api/artifacts.ts');
const tasks = await import('../src/lib/api/tasks.ts');
const { subscribeSSE } = await import('../src/lib/api/sse.ts');

const { ApiError, NativeRecoveryError, request } = client;

// --- fetch stub ------------------------------------------------------------
let calls = [];
let queue = [];
function json(status, body, headers = {}) {
  return () => new Response(body === undefined ? null : JSON.stringify(body), {
    status, headers: { 'Content-Type': 'application/json', ...headers },
  });
}
function text(status, body) {
  return () => new Response(body, { status, headers: { 'Content-Type': 'text/plain' } });
}
function networkFailure() {
  return () => { throw new TypeError('Failed to fetch'); };
}
globalThis.fetch = async (url, init = {}) => {
  calls.push({ url: String(url), auth: new Headers(init.headers).get('Authorization') });
  const next = queue.shift();
  if (!next) throw new Error(`unexpected fetch ${url}`);
  return next();
};

beforeEach(() => {
  calls = [];
  queue = [];
  auth.__resetTokenCacheForTests();
  recovery.__resetNativeRecoveryForTests();
  sessionStorage.setItem('happyranch.token', 'tok-1');
});

const PATHS = (c) => c.map((x) => new URL(x.url, 'http://h').pathname);

// --- isCompatible ----------------------------------------------------------
test('contract owner constants', () => {
  assert.equal(CLIENT_CONTRACT, 1);
  assert.equal(MIN_DAEMON, 1);
});

test('isCompatible table (min_client <= 1 <= current and current >= 1; missing/invalid -> false)', () => {
  const rows = [
    [{ api_contract: { current: 1, min_client: 1 } }, true],
    [{ api_contract: { current: 2, min_client: 1 } }, true],
    [{ api_contract: { current: 5, min_client: 0 } }, true],
    [{ api_contract: { current: 2, min_client: 2 } }, false], // client cut off
    [{ api_contract: { current: 0, min_client: 0 } }, false], // daemon too old
    [{ api_contract: { current: 0, min_client: 1 } }, false],
    [{ status: 'ok' }, false], // no metadata: no assumed legacy contract
    [{ api_contract: null }, false],
    [{ api_contract: 'v1' }, false],
    [{ api_contract: { current: '1', min_client: 1 } }, false],
    [{ api_contract: { current: 1.5, min_client: 1 } }, false],
    [{ api_contract: { current: 1 } }, false],
    [{ api_contract: { min_client: 1 } }, false],
    [null, false],
    [undefined, false],
    ['ok', false],
  ];
  for (const [health, expected] of rows) {
    assert.equal(isCompatible(health), expected, JSON.stringify(health));
  }
});

// --- parser ----------------------------------------------------------------
test('parseNativeRecovery recognizes only the exact contract shape', () => {
  const p = recovery.parseNativeRecovery;
  for (const code of ['no_target', 'target_starting', 'target_unreachable', 'busy']) {
    assert.deepEqual(p(503, { detail: { code } }), { code });
    assert.equal(p(409, { detail: { code } }), null, `409 ${code}`);
    assert.equal(p(503, { detail: { code, extra: 1 } }), null, `extra key ${code}`);
  }
  assert.deepEqual(p(409, { detail: { code: 'target_changed' } }), { code: 'target_changed' });
  assert.equal(p(503, { detail: { code: 'target_changed' } }), null);
  assert.equal(p(409, { detail: { code: 'target_changed', side: 'host' } }), null);
  assert.deepEqual(p(503, { detail: { code: 'incompatible' } }), { code: 'incompatible', side: 'unknown' });
  for (const side of ['host', 'app', 'unknown']) {
    assert.deepEqual(p(503, { detail: { code: 'incompatible', side } }), { code: 'incompatible', side });
  }
  assert.deepEqual(p(503, { detail: { code: 'incompatible', side: 'daemon' } }), { code: 'incompatible', side: 'unknown' });
  assert.equal(p(503, { detail: { code: 'incompatible', side: 'host', why: 'x' } }), null);
  // Daemon-shaped errors that share a status are not recognized.
  assert.equal(p(503, { detail: { code: 'org_unavailable' } }), null);
  assert.equal(p(503, { detail: { code: 'naming_unavailable' } }), null);
  assert.equal(p(503, { detail: 'no_target' }), null);
  assert.equal(p(503, { detail: { code: 'busy' }, extra: true }), null);
  assert.equal(p(503, { code: 'busy' }), null);
  assert.equal(p(503, null), null);
  assert.equal(p(503, 'busy'), null);
  assert.equal(p(409, { detail: { code: 'conflict' } }), null);
  for (const status of [200, 400, 401, 404, 500, 502]) {
    assert.equal(p(status, { detail: { code: 'no_target' } }), null, `status ${status}`);
  }
});

test('store: latest wins, clear notifies, identical report is a no-op', () => {
  let n = 0;
  const off = recovery.subscribeNativeRecovery(() => { n += 1; });
  recovery.reportNativeRecovery({ code: 'busy' });
  const first = recovery.getNativeRecoveryState();
  recovery.reportNativeRecovery({ code: 'busy' });
  assert.equal(recovery.getNativeRecoveryState(), first);
  recovery.reportNativeRecovery({ code: 'incompatible', side: 'app' });
  assert.deepEqual(recovery.getNativeRecoveryState(), { code: 'incompatible', side: 'app' });
  recovery.clearNativeRecovery();
  assert.equal(recovery.getNativeRecoveryState(), null);
  assert.equal(n, 3);
  off();
});

test('store: explicit retry clears the state before notifying latched read owners', () => {
  const seen = [];
  const off = recovery.subscribeNativeRecoveryRetry(() => { seen.push(recovery.getNativeRecoveryState()); });
  recovery.reportNativeRecovery({ code: 'target_unreachable' });
  recovery.retryNativeRecovery();
  // The owner runs once, with the state already cleared, so its own read can
  // report the state again if the target still answers with a recovery code.
  assert.deepEqual(seen, [null]);
  assert.equal(recovery.getNativeRecoveryState(), null);
  off();
  recovery.retryNativeRecovery();
  assert.equal(seen.length, 1, 'unsubscribed owner is not retried');
  // A report alone never triggers retry listeners.
  recovery.subscribeNativeRecoveryRetry(() => { seen.push('unexpected'); });
  recovery.reportNativeRecovery({ code: 'busy' });
  assert.equal(seen.length, 1);
});

// --- client.request --------------------------------------------------------
test('request: 503 native code -> NativeRecoveryError, state recorded, bearer kept, single send', async () => {
  queue.push(json(503, { detail: { code: 'no_target' } }));
  const err = await request('/orgs').catch((e) => e);
  assert.ok(err instanceof NativeRecoveryError);
  assert.ok(err instanceof ApiError);
  assert.equal(err.status, 503);
  assert.equal(err.code, 'no_target');
  assert.deepEqual(err.recovery, { code: 'no_target' });
  assert.deepEqual(recovery.getNativeRecoveryState(), { code: 'no_target' });
  assert.equal(sessionStorage.getItem('happyranch.token'), 'tok-1');
  assert.equal(calls.length, 1);
});

test('request: 409 target_changed and 503 incompatible side carry through', async () => {
  queue.push(json(409, { detail: { code: 'target_changed' } }));
  const a = await request('/orgs', { method: 'POST', body: {} }).catch((e) => e);
  assert.ok(a instanceof NativeRecoveryError);
  assert.equal(a.status, 409);
  queue.push(json(503, { detail: { code: 'incompatible', side: 'host' } }));
  const b = await request('/orgs').catch((e) => e);
  assert.deepEqual(b.recovery, { code: 'incompatible', side: 'host' });
  assert.deepEqual(b.detail, { code: 'incompatible', side: 'host' });
  assert.equal(sessionStorage.getItem('happyranch.token'), 'tok-1');
});

test('request: ordinary 503/409 unchanged (plain ApiError, no state)', async () => {
  queue.push(json(503, { detail: { code: 'org_unavailable' } }));
  const a = await request('/x').catch((e) => e);
  assert.ok(a instanceof ApiError && !('recovery' in a));
  assert.equal(a.code, 'org_unavailable');
  queue.push(json(503, { detail: 'acquisition_unavailable' }));
  const b = await request('/x').catch((e) => e);
  assert.ok(!('recovery' in b));
  assert.equal(b.detail, 'acquisition_unavailable');
  queue.push(json(409, { detail: { code: 'stale_generation', current: 3 } }));
  const c = await request('/x').catch((e) => e);
  assert.ok(!('recovery' in c));
  assert.equal(c.status, 409);
  queue.push(text(503, 'Service Unavailable'));
  const d = await request('/x').catch((e) => e);
  assert.ok(!('recovery' in d));
  assert.equal(d.detail, 'Service Unavailable');
  assert.equal(recovery.getNativeRecoveryState(), null);
  assert.equal(sessionStorage.getItem('happyranch.token'), 'tok-1');
});

test('request: 401 still clears, re-bootstraps and replays once (unchanged)', async () => {
  queue.push(json(401, { detail: 'bad token' }));
  queue.push(json(200, { token: 'tok-2' })); // bootstrap
  queue.push(json(200, { ok: true }));
  const out = await request('/x');
  assert.deepEqual(out, { ok: true });
  assert.deepEqual(PATHS(calls), ['/api/v1/x', '/api/v1/auth/bootstrap', '/api/v1/x']);
  assert.equal(calls[2].auth, 'Bearer tok-2');
  assert.equal(recovery.getNativeRecoveryState(), null);
});

test('request: network failure propagates unchanged, no state', async () => {
  queue.push(networkFailure());
  const err = await request('/x').catch((e) => e);
  assert.ok(err instanceof TypeError);
  assert.equal(recovery.getNativeRecoveryState(), null);
});

test('bootstrap: native code recorded as recovery state, still BootstrapError', async () => {
  sessionStorage.removeItem('happyranch.token');
  queue.push(json(503, { detail: { code: 'target_starting' } }));
  const err = await request('/x').catch((e) => e);
  assert.ok(err instanceof auth.BootstrapError);
  assert.equal(err.status, 503);
  assert.deepEqual(recovery.getNativeRecoveryState(), { code: 'target_starting' });
  // Ordinary bootstrap failure records nothing.
  recovery.__resetNativeRecoveryForTests();
  queue.push(json(403, { detail: 'forbidden' }));
  const e2 = await auth.getToken().catch((e) => e);
  assert.ok(e2 instanceof auth.BootstrapError);
  assert.equal(recovery.getNativeRecoveryState(), null);
});

// --- artifacts direct fetch paths -------------------------------------------
test('artifacts: list/download/delete/upload/thread download recognize native codes', async () => {
  const cases = [
    ['list', () => artifacts.listArtifacts('demo'), json(503, { detail: { code: 'busy' } })],
    ['download', () => artifacts.downloadArtifact('demo', 'a.txt'), json(503, { detail: { code: 'incompatible', side: 'app' } })],
    ['thread', () => artifacts.downloadThreadAttachment('demo', 'THR-1', 'att', 'a.txt'), json(503, { detail: { code: 'target_unreachable' } })],
    ['delete', () => artifacts.deleteArtifact('demo', 'a.txt'), json(409, { detail: { code: 'target_changed' } })],
    ['upload', () => artifacts.uploadArtifact('demo', { file: new File(['x'], 'a.txt'), agent: 'founder' }), json(503, { detail: { code: 'no_target' } })],
  ];
  for (const [name, call, response] of cases) {
    recovery.__resetNativeRecoveryForTests();
    calls = [];
    queue.push(response);
    const err = await call().catch((e) => e);
    assert.ok(err instanceof NativeRecoveryError, name);
    assert.notEqual(recovery.getNativeRecoveryState(), null, name);
    assert.equal(calls.length, 1, name);
    assert.equal(sessionStorage.getItem('happyranch.token'), 'tok-1', name);
  }
});

test('artifacts: ordinary errors and 401 retry unchanged', async () => {
  queue.push(json(503, { detail: { code: 'artifact_store_unavailable' } }));
  const a = await artifacts.listArtifacts('demo').catch((e) => e);
  assert.ok(a instanceof ApiError && !('recovery' in a));
  assert.equal(a.code, 'artifact_store_unavailable');
  calls = [];
  queue.push(json(401, null), json(200, { token: 'tok-3' }), json(200, { artifacts: [] }));
  assert.deepEqual(await artifacts.listArtifacts('demo'), { artifacts: [] });
  assert.deepEqual(PATHS(calls), ['/api/v1/orgs/demo/artifacts', '/api/v1/auth/bootstrap', '/api/v1/orgs/demo/artifacts']);
  queue.push(networkFailure());
  assert.ok((await artifacts.deleteArtifact('demo', 'a').catch((e) => e)) instanceof TypeError);
  assert.equal(recovery.getNativeRecoveryState(), null);
});

// --- tasks.uploadTaskAttachment direct fetch --------------------------------
test('tasks upload: native code -> NativeRecoveryError; ordinary failure keeps Error+status', async () => {
  queue.push(json(503, { detail: { code: 'target_unreachable' } }));
  const a = await tasks.uploadTaskAttachment('demo', new File(['x'], 'a.txt')).catch((e) => e);
  assert.ok(a instanceof NativeRecoveryError);
  assert.deepEqual(recovery.getNativeRecoveryState(), { code: 'target_unreachable' });
  recovery.__resetNativeRecoveryForTests();
  queue.push(json(503, { detail: 'store offline' }));
  const b = await tasks.uploadTaskAttachment('demo', new File(['x'], 'a.txt')).catch((e) => e);
  assert.ok(!(b instanceof ApiError));
  assert.equal(b.status, 503);
  assert.equal(b.message, 'Upload failed: 503');
  queue.push(json(409, { detail: { code: 'target_changed' } }));
  const c = await tasks.uploadTaskAttachment('demo', new File(['x'], 'a.txt')).catch((e) => e);
  assert.ok(c instanceof NativeRecoveryError);
  assert.equal(c.status, 409);
});

// --- subscribeSSE -----------------------------------------------------------
test('SSE: native code at open stops (single attempt) and surfaces state', async () => {
  queue.push(json(503, { detail: { code: 'incompatible', side: 'unknown' } }));
  const ctl = new AbortController();
  const err = await subscribeSSE('/orgs/demo/threads/events', { signal: ctl.signal, onMessage() {} }).catch((e) => e);
  assert.ok(err instanceof NativeRecoveryError);
  assert.deepEqual(recovery.getNativeRecoveryState(), { code: 'incompatible', side: 'unknown' });
  await new Promise((r) => setTimeout(r, 1200)); // past the default 1s retry interval
  assert.equal(calls.length, 1);
  assert.equal(sessionStorage.getItem('happyranch.token'), 'tok-1');
  ctl.abort();
});

test('SSE: ordinary 503/409 open failure unchanged (single-shot fatal, no state)', async () => {
  for (const response of [json(503, { detail: 'down' }), json(409, { detail: { code: 'other' } })]) {
    calls = [];
    queue.push(response);
    const ctl = new AbortController();
    const err = await subscribeSSE('/x', { signal: ctl.signal, onMessage() {} }).catch((e) => e);
    assert.ok(err instanceof Error && !(err instanceof ApiError));
    assert.match(err.message, /SSE open failed \((503|409)\)/);
    assert.equal(calls.length, 1);
    ctl.abort();
  }
  assert.equal(recovery.getNativeRecoveryState(), null);
});

test('SSE: 401 still clears the bearer and fails once (unchanged)', async () => {
  queue.push(json(401, null));
  const ctl = new AbortController();
  const err = await subscribeSSE('/x', { signal: ctl.signal, onMessage() {} }).catch((e) => e);
  assert.equal(err.message, 'token expired');
  assert.equal(sessionStorage.getItem('happyranch.token'), null);
  ctl.abort();
});

test('SSE: network failure still retries (unchanged)', async () => {
  queue.push(networkFailure());
  queue.push(() => new Promise(() => {})); // second attempt hangs until abort
  const ctl = new AbortController();
  const pending = subscribeSSE('/x', { signal: ctl.signal, onMessage() {} });
  await new Promise((r) => setTimeout(r, 1300));
  assert.equal(calls.length, 2);
  assert.equal(recovery.getNativeRecoveryState(), null);
  ctl.abort();
  await Promise.race([pending, new Promise((r) => setTimeout(r, 50))]);
});
