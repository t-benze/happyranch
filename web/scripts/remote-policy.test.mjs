// Focused Node 24 checks for THR-387 S8-2b (web remote read-only policy
// module, `web/src/lib/remotePolicy`): the module loads the SAME authoritative
// `protocol/remote-client-config-policy.v1.json` (through the
// `@happyranch/remote-client-config-policy` build alias), every UI-named
// operation is a policy entry and the 21 entries are all named, concrete
// request matching follows the native template semantics, and the pre-forward
// `403 remote_read_only` refusal is recognised and keeps the draft (a
// confirmed failure, never unknown). Local evidence only (not wired into CI,
// no Vitest or Product E2E selection). Run from the repository root:
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
  }
});

test('operations, reads and near-misses do not match', () => {
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

test('the pre-forward refusal is recognised and is a confirmed failure (draft kept)', () => {
  const refusal = { name: 'ApiError', status: 403, code: 'remote_read_only', detail: { code: 'remote_read_only' } };
  assert.equal(isRemoteReadOnlyError(refusal), true);
  assert.equal(classifySaveError(refusal), 'failed');
  assert.equal(isRemoteReadOnlyError({ status: 403, code: 'forbidden' }), false);
  assert.equal(isRemoteReadOnlyError({ status: 503, code: 'remote_read_only' }), false);
  assert.equal(isRemoteReadOnlyError(null), false);
  assert.equal(isRemoteReadOnlyError(new Error('remote_read_only')), false);
});
