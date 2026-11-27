// Focused Node 24 checks for THR-387 S8-2b (web remote read-only policy
// module, `web/src/lib/remotePolicy`): the module loads the SAME authoritative
// `protocol/remote-client-config-policy.v1.json` (through the
// `@happyranch/remote-client-config-policy` build alias), every UI-named
// operation is a policy entry and the 21 entries are all named, concrete web
// requests match the policy templates, and `403 remote_read_only` errors are
// recognised and classified as failed. Synthetic responses also exercise the
// refusal selectors below, without rendering UI or checking retained drafts.
// Local evidence only (not wired into CI; no Vitest or Product E2E selection).
// Run from the repository root:
//   node --experimental-transform-types --test web/scripts/remote-policy.test.mjs
import { registerHooks } from 'node:module';
import { existsSync, readFileSync } from 'node:fs';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { dirname, resolve as resolvePath } from 'node:path';
import { test } from 'node:test';
import assert from 'node:assert/strict';

const HERE = dirname(fileURLToPath(import.meta.url));
const SRC = resolvePath(HERE, '..', 'src');
const ROOT = resolvePath(HERE, '..', '..');
const POLICY_PATH = resolvePath(ROOT, 'protocol/remote-client-config-policy.v1.json');
const POLICY_URL = pathToFileURL(POLICY_PATH).href;

// Mirror the Vite alias (`vite.config.ts`) and resolve `@/` / extensionless
// relative imports from .ts modules; serve the policy JSON as a module.
registerHooks({
  resolve(specifier, context, nextResolve) {
    if (specifier === '@happyranch/remote-client-config-policy') return { url: POLICY_URL, shortCircuit: true };
    const parent = context.parentURL ? context.parentURL.replace(/\?.*$/, '') : '';
    let base = null;
    if (specifier.startsWith('@/')) base = resolvePath(SRC, specifier.slice(2));
    else if ((specifier.startsWith('./') || specifier.startsWith('../')) && parent.endsWith('.ts')) {
      base = resolvePath(dirname(fileURLToPath(parent)), specifier);
    }
    if (base && !/\.[cm]?[jt]s$/.test(base)) {
      for (const candidate of [`${base}.ts`, `${base}/index.ts`]) {
        if (existsSync(candidate)) return nextResolve(pathToFileURL(candidate).href, context);
      }
    }
    return nextResolve(specifier, context);
  },
  load(url, context, nextLoad) {
    if (url === POLICY_URL) {
      return { format: 'module', source: `export default ${readFileSync(POLICY_PATH, 'utf8')};`, shortCircuit: true };
    }
    return nextLoad(url, context);
  },
});

const policyModule = await import('../src/lib/remotePolicy/policy.ts');
const { REMOTE_CONFIG_ENTRIES, isRemoteConfigOperation, matchesRemoteConfigRequest, isRemoteReadOnlyError } = policyModule;
const { classifySaveError } = await import('../src/lib/owners/ownerRegistry.ts');
const indexSource = readFileSync(resolvePath(SRC, 'lib/remotePolicy/index.ts'), 'utf8');

const FILE = JSON.parse(readFileSync(POLICY_PATH, 'utf8'));

function concrete(template) {
  return template.replace(/\{[^}]+\}/g, (name) => `x-${name.slice(1, -1)}`);
}

test('the module exposes exactly the shipped policy file entries', () => {
  assert.equal(FILE.version, 1);
  assert.equal(FILE.entries.length, 21);
  assert.deepEqual(
    REMOTE_CONFIG_ENTRIES.map((e) => ({ ...e })),
    FILE.entries,
  );
});

test('every UI-named REMOTE_OPS operation is a policy entry and all 21 are named', () => {
  const named = [...indexSource.matchAll(/^\s+\w+: '((?:DELETE|PATCH|POST|PUT) \/api\/v1\/[^']+)',$/gm)].map((m) => m[1]);
  const entries = FILE.entries.map((e) => `${e.method} ${e.path_template}`);
  for (const op of named) assert.ok(isRemoteConfigOperation(op), `not a policy entry: ${op}`);
  assert.deepEqual([...new Set(named)].sort(), [...entries].sort());
  assert.equal(isRemoteConfigOperation('POST /api/v1/orgs/{slug}/schedules/{schedule_id}/pause'), false);
  assert.equal(isRemoteConfigOperation('GET /api/v1/orgs/{slug}/settings'), false);
});

test('concrete config requests match, with or without /api/v1 and a query', () => {
  for (const entry of FILE.entries) {
    const path = concrete(entry.path_template);
    assert.ok(matchesRemoteConfigRequest(entry.method, path), `${entry.method} ${path}`);
    assert.ok(matchesRemoteConfigRequest(entry.method.toLowerCase(), path.replace('/api/v1', '')), `${entry.method} bare ${path}`);
    assert.ok(matchesRemoteConfigRequest(entry.method, `${path}?a=1`), `${entry.method} ${path}?a=1`);
    assert.ok(matchesRemoteConfigRequest(entry.method, `${path}/`), `${entry.method} ${path}/`);
  }
});

test('listed non-config operations, reads and path near-misses do not match', () => {
  const misses = [
    ['GET', '/api/v1/orgs/s/settings/org'],
    ['GET', '/api/v1/orgs/s/schedules/abc'],
    ['POST', '/api/v1/orgs/s/schedules/abc/pause'],
    ['POST', '/api/v1/orgs/s/schedules/abc/cancel'],
    ['POST', '/api/v1/orgs/s/schedules/abc/renew'],
    ['POST', '/api/v1/executor-binaries/validate'],
    ['POST', '/api/v1/orgs/s/custom-skills/k/eligibility/preview'],
    ['POST', '/api/v1/orgs/s/custom-skills/k/purge'],
    ['POST', '/api/v1/orgs/s/agents/a/approve'],
    ['POST', '/api/v1/orgs/s/agents/a/reject'],
    ['POST', '/api/v1/orgs/s/tasks'],
    ['POST', '/api/v1/runtime/custom-cli/op/commit'],
    ['PUT', '/api/v1/orgs/s/settings/daemon-capacity'],
    ['PUT', '/api/v1/orgs/s/settings/org/extra'],
    ['PUT', '/api/v1/orgs//settings/org'],
    ['DELETE', '/api/v1/executor-binaries'],
    ['POST', '/api/v1/orgs/s'],
  ];
  for (const [method, path] of misses) assert.equal(matchesRemoteConfigRequest(method, path), false, `${method} ${path}`);
});

test('a 403 remote_read_only error is recognised and classified as failed', () => {
  const refusal = { name: 'ApiError', status: 403, code: 'remote_read_only', detail: { code: 'remote_read_only' } };
  assert.equal(isRemoteReadOnlyError(refusal), true);
  assert.equal(classifySaveError(refusal), 'failed');
  assert.equal(isRemoteReadOnlyError({ status: 403, code: 'forbidden' }), false);
  assert.equal(isRemoteReadOnlyError({ status: 503, code: 'remote_read_only' }), false);
  assert.equal(isRemoteReadOnlyError(null), false);
  assert.equal(isRemoteReadOnlyError(new Error('remote_read_only')), false);
});

// --- Review F2/F3: refusal selection from synthetic 403 responses -------------
// request() uses the real client and native target store: a synthetic bridge
// reports local status while synthetic fetch returns 403 remote_read_only.
// No native target switch or app.stateChanged event is executed. These checks
// use real owner-registry status derivation and the refusal selectors consumed
// by useConfigOwner/useRemoteRefusal (`lib/remotePolicy/refusal.ts`), without React.
class MemoryStorage {
  #m = new Map();
  getItem(k) { return this.#m.has(k) ? this.#m.get(k) : null; }
  setItem(k, v) { this.#m.set(k, String(v)); }
  removeItem(k) { this.#m.delete(k); }
}
async function refusedBeforeStatusEvent(path, body) {
  globalThis.window = globalThis;
  globalThis.sessionStorage = new MemoryStorage();
  globalThis.sessionStorage.setItem('happyranch.token', 'synthetic-not-a-credential');
  globalThis.document = { hidden: false, addEventListener() {}, removeEventListener() {} };
  Object.defineProperty(globalThis, 'location', { configurable: true, writable: true, value: { protocol: 'happyranch-app:', origin: 'happyranch-app://app' } });
  const target = await import('../src/lib/native/target.ts');
  const client = await import('../src/lib/api/client.ts');
  const local = { schema: 2, generation: 'g1', target: { kind: 'local', id: 'local', name: 'This computer' }, state: 'ready', incompatible: null, local: { supported: true, mode: 'service', running: 'running', prompt_base_url: 'http://127.0.0.1:9876/api/v1' }, candidate: null, access_unsaved: false, locale: { current: 'en', persisted: true } };
  target.__setNativeBridgeForTests({ call: async () => JSON.stringify(local), on: () => () => {} });
  await target.captureTarget();
  let sends = 0;
  globalThis.fetch = async () => {
    sends += 1;
    return new Response(JSON.stringify({ detail: { code: 'remote_read_only' } }), { status: 403, headers: { 'Content-Type': 'application/json' } });
  };
  const error = await client.request(path, { method: path.endsWith('/orgs') ? 'POST' : 'DELETE', body }).catch((e) => e);
  return { error, sends, app: target.getAppStatus() };
}

test('F2: a profile-removal 403 yields a draftless refusal selected for display while app status is local', async () => {
  const { remoteRefusalFor, shownRemoteRefusal } = await import('../src/lib/remotePolicy/refusal.ts');
  const { createOwnerRegistry } = await import('../src/lib/owners/ownerRegistry.ts');
  const { error, sends, app } = await refusedBeforeStatusEvent('/executors/runtime/profiles/studio-cli');
  assert.equal(sends, 1);
  assert.equal(app.target.kind, 'local', 'the page has not seen the remote target yet');
  const registry = createOwnerRegistry();
  registry.register('remove', { scope: 'settings', page: 'settings.executors', subject: 'studio-cli', org: 'studio', computer: null });
  registry.setDraft('remove', { dirty: false, revision: 0 });
  const ticket = registry.beginSave('remove');
  registry.settleSave(ticket, classifySaveError(error));
  const status = registry.getSnapshot()[0].status;
  assert.notEqual(status, 'failed', 'the registry derives no failed status without a draft');
  const refusal = remoteRefusalFor(error, app, true);
  assert.deepEqual(refusal, { host: '', draftless: true }, 'Host not named yet: the UI shows the localized fallback');
  assert.deepEqual(shownRemoteRefusal(status, refusal), refusal, 'shown independently of failed/unsent-draft status');
  // Pending status and an absent refusal record both hide the refusal.
  assert.equal(shownRemoteRefusal('pending', refusal), null);
  assert.equal(shownRemoteRefusal(status, null), null);
});

test('F2 control: draft refusals require failed status; dirty/pending/unknown/missing status hides draftless refusals', async () => {
  const { shownRemoteRefusal } = await import('../src/lib/remotePolicy/refusal.ts');
  const draft = { host: 'Studio Host', draftless: false };
  assert.deepEqual(shownRemoteRefusal('failed', draft), draft);
  for (const status of ['clean', 'confirmed', 'dirty', 'pending', 'unknown', undefined]) {
    assert.equal(shownRemoteRefusal(status, draft), null, String(status));
  }
  const action = { host: '', draftless: true };
  for (const status of ['dirty', 'pending', 'unknown', undefined]) assert.equal(shownRemoteRefusal(status, action), null, String(status));
});

test('F3: an organization-create 403 is classified as raw and yields refusal records for local or remote status', async () => {
  const { remoteRefusalFor } = await import('../src/lib/remotePolicy/refusal.ts');
  const { classifyAddOrgError } = await import('../src/lib/addOrgError.ts');
  const { error, sends, app } = await refusedBeforeStatusEvent('/orgs', { slug: 'northwind' });
  assert.equal(sends, 1, 'not replayed');
  assert.equal(app.target.kind, 'local');
  // The organization error classifier returns raw; refusal selection is separate.
  assert.equal(classifyAddOrgError(error, 'northwind').kind, 'raw');
  assert.deepEqual(remoteRefusalFor(error, app, false), { host: '', draftless: false });
  const remoteApp = { ...app, target: { kind: 'remote', id: 'saved', name: 'Studio Host' } };
  assert.deepEqual(remoteRefusalFor(error, remoteApp, false), { host: 'Studio Host', draftless: false });
  for (const other of [{ status: 403, code: 'forbidden' }, { status: 409, code: 'org_exists' }, new Error('remote_read_only'), null]) {
    assert.equal(remoteRefusalFor(other, app, false), null);
  }
});
