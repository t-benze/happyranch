// Focused Node 24 check for the THR-402 Create team / Create agent form rules
// (`web/src/features/agents/createAgentForm.ts`): the new-input grammars the
// backend shares, the editable team suggestion, the one-team-field request
// body, the outcome classes (refused / setup incomplete / unknown) and the
// Check result matcher, plus the recovery boundaries of review TASK-19419:
// incomplete naming is never absence (F1), Check result / success readback
// use reads issued after retiring earlier in-flight reads (F2, real TanStack
// QueryClient), and the transport's typed pre-forward errors are "not sent"
// (F3, real client.ts error classes). Local evidence only (not wired into CI).
// Run from the repository root:
//   node --experimental-transform-types --test web/scripts/create-agent-form.test.mjs
import { registerHooks } from 'node:module';
import { existsSync } from 'node:fs';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { dirname, resolve as resolvePath } from 'node:path';
import { test } from 'node:test';
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

// Browser globals the transport modules touch at load.
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

const form = await import('../src/features/agents/createAgentForm.ts');
const client = await import('../src/lib/api/client.ts');
const { freshReads, refreshAfterMutation } = await import('../src/lib/freshReads.ts');
const { QueryClient, QueryObserver } = await import('@tanstack/react-query');

test('agent name: 1-64 ASCII, letter first, then letters/digits/_/-', () => {
  const cases = [
    ['', 'required'],
    ['a', null],
    ['A'.repeat(64), null],
    ['A'.repeat(65), 'invalid'],
    ['Consultant-Claude_2', null],
    ['1abc', 'invalid'],
    ['_abc', 'invalid'],
    ['ab c', 'invalid'],
    [' abc', 'invalid'],
    ['abç', 'invalid'],
    ['ab.c', 'invalid'],
  ];
  for (const [name, problem] of cases) assert.equal(form.agentNameProblem(name), problem, JSON.stringify(name));
});

test('new team name: shared grammar, default reserved in any case, duplicates refused', () => {
  const existing = ['engineering', 'Legacy-Team'];
  const cases = [
    ['', 'required'],
    ['a', null],
    ['a'.repeat(64), null],
    ['a'.repeat(65), 'invalid'],
    ['dev_ops2', null],
    ['Dev', 'invalid'],
    ['dev-ops', 'invalid'],
    ['dev ops', 'invalid'],
    ['2dev', 'invalid'],
    ['_dev', 'invalid'],
    ['default', 'reserved'],
    ['Default', 'reserved'],
    ['DEFAULT', 'reserved'],
    ['engineering', 'exists'],
  ];
  for (const [team, problem] of cases) assert.equal(form.newTeamProblem(team, existing), problem, JSON.stringify(team));
  // Unknown roster: no duplicate claim (the server stays authoritative).
  assert.equal(form.newTeamProblem('engineering', null), null);
});

test('team suggestion: lowercase, hyphen to underscore, drop the last _suffix', () => {
  assert.equal(form.suggestTeamName(''), '');
  assert.equal(form.suggestTeamName('Alpha'), 'alpha');
  assert.equal(form.suggestTeamName('Dev-Lead'), 'dev');
  assert.equal(form.suggestTeamName('growth_ops_manager'), 'growth_ops');
  assert.equal(form.suggestTeamName('x_'), 'x');
});

test('request body carries exactly one team field for the current role', () => {
  const draft = {
    name: 'Ada', team: 'engineering', newTeam: 'research', executor: 'claude',
    description: 'd', systemPrompt: 'p',
  };
  const worker = form.buildCreateBody({ ...draft, role: 'worker' });
  assert.deepEqual(worker, { display_name: 'Ada', role: 'worker', team: 'engineering', executor: 'claude', description: 'd', system_prompt: 'p' });
  assert.equal('new_team' in worker, false, 'a worker never submits the hidden new team name');
  const manager = form.buildCreateBody({ ...draft, role: 'manager' });
  assert.deepEqual(manager, { display_name: 'Ada', role: 'manager', new_team: 'research', executor: 'claude', description: 'd', system_prompt: 'p' });
  assert.equal('team' in manager, false);
  assert.equal('name' in manager, false, 'never manufactures a permanent ID');
});

test('server codes map to their field; unrelated codes to none', () => {
  const expect = {
    invalid_team_name: 'newTeam', team_reserved: 'newTeam', team_exists: 'newTeam',
    unknown_team: 'team', agent_exists: 'name', identity_name_unavailable: 'name',
    identity_name_reserved: 'name', invalid_identity_label: 'name', agent_id_exhausted: 'name',
    invalid_executor: 'executor', missing_required_field: null, role_team_mismatch: null,
    something_new: null, constructor: null,
  };
  for (const [code, field] of Object.entries(expect)) assert.equal(form.fieldForCode(code), field, code);
  assert.equal(form.fieldForCode(null), null);
});

const apiError = (status, code, detail = code ? { code } : null) => ({ name: 'ApiError', status, code, detail });

test('outcome classes: refused before effect, setup incomplete, unknown', () => {
  assert.deepEqual(form.classifyCreateError(apiError(422, 'invalid_team_name')), { kind: 'refused', status: 422, code: 'invalid_team_name' });
  assert.deepEqual(form.classifyCreateError(apiError(409, 'team_reserved')), { kind: 'refused', status: 409, code: 'team_reserved' });
  assert.deepEqual(form.classifyCreateError(apiError(404, 'unknown_team')), { kind: 'refused', status: 404, code: 'unknown_team' });
  assert.deepEqual(form.classifyCreateError(apiError(401, null)), { kind: 'refused', status: 401, code: null });
  assert.deepEqual(form.classifyCreateError(apiError(403, 'remote_read_only')), { kind: 'refused', status: 403, code: 'remote_read_only' });
  assert.deepEqual(
    form.classifyCreateError(apiError(500, 'agent_setup_incomplete', { code: 'agent_setup_incomplete', canonical_id: 'ada', team: 'research', role: 'manager' })),
    { kind: 'setupIncomplete', canonicalId: 'ada', team: 'research', role: 'manager' },
  );
  for (const err of [
    apiError(500, null), apiError(503, 'upstream'), apiError(408, null), apiError(499, 'cancelled'),
    apiError(409, 'target_changed'), new TypeError('Failed to fetch'), null, 'boom',
  ]) {
    assert.deepEqual(form.classifyCreateError(err), { kind: 'unknown' }, JSON.stringify(err));
  }
});

test('Check result: full match resolves, a name alone never does, absence means not created', () => {
  const sent = form.buildCreateBody({
    name: 'Ada', role: 'manager', team: '', newTeam: 'research', executor: 'claude',
    description: 'Leads research', systemPrompt: 'You lead research.',
  });
  const rows = [
    { canonical_id: 'ada', kind: 'agent', addressable_name: 'Ada', naming_status: 'ready' },
    { canonical_id: 'founder', kind: 'founder', addressable_name: 'Founder', naming_status: 'ready' },
  ];
  const identities = { identities: rows, naming_diagnostic: null };
  const row = {
    name: 'ada', addressable_name: 'Ada', team: 'research', role: 'manager', executor: 'claude',
    description: 'Leads research', system_prompt: 'You lead research.',
  };
  assert.deepEqual(form.findCreatedAgent(sent, [row], identities), { kind: 'matched', canonicalId: 'ada' });
  // Case-insensitive name, metadata missing on an older roster (identity fallback).
  assert.deepEqual(
    form.findCreatedAgent({ ...sent, display_name: 'ADA' }, [{ ...row, addressable_name: undefined }], identities),
    { kind: 'matched', canonicalId: 'ada' },
  );
  assert.deepEqual(form.findCreatedAgent(sent, [{ ...row, team: 'other' }], identities), { kind: 'nameOnly', canonicalId: 'ada' });
  assert.deepEqual(form.findCreatedAgent(sent, [{ ...row, system_prompt: 'else' }], identities), { kind: 'nameOnly', canonicalId: 'ada' });
  // Only a (e.g. terminated) identity carries the name: still not proof.
  assert.deepEqual(form.findCreatedAgent(sent, [], identities), { kind: 'nameOnly', canonicalId: 'ada' });
  assert.deepEqual(form.findCreatedAgent(sent, [], { identities: rows.slice(1), naming_diagnostic: null }), { kind: 'absent' });
});

// ---- F1: incomplete naming observations are never evidence of absence ----

test('F1 Check result: unavailable/missing naming metadata keeps the result unknown', () => {
  const sent = form.buildCreateBody({
    name: 'Retained-Bot', role: 'worker', team: 'engineering', newTeam: '', executor: 'claude',
    description: 'd', systemPrompt: 'p',
  });
  // The real producer's unavailable shape (runtime/identities/registry.py
  // summary_name_metadata / current_identity_records): canonical rows with
  // addressable_name=null, naming_status=unavailable, plus a diagnostic.
  const retainedRow = {
    name: 'retained_bot', addressable_name: null, name_revision: null, naming_status: 'unavailable',
    team: 'engineering', role: 'worker', executor: 'claude', description: 'd', system_prompt: 'p',
  };
  const retainedIdentity = { canonical_id: 'retained_bot', kind: 'agent', addressable_name: null, naming_status: 'unavailable' };
  const founder = { canonical_id: 'founder', kind: 'founder', addressable_name: 'Founder', naming_status: 'ready' };
  const unavailable = { identities: [founder, retainedIdentity], naming_diagnostic: 'naming_lookup_unavailable' };
  assert.deepEqual(form.findCreatedAgent(sent, [retainedRow], unavailable), { kind: 'incomplete' });
  // Same rows with no diagnostic: the unavailable rows alone still block absence.
  assert.deepEqual(form.findCreatedAgent(sent, [retainedRow], { ...unavailable, naming_diagnostic: null }), { kind: 'incomplete' });
  // A diagnostic alone (all rows look ready) blocks absence.
  const readyOther = { canonical_id: 'other', kind: 'agent', addressable_name: 'Other', naming_status: 'ready' };
  assert.deepEqual(form.findCreatedAgent(sent, [], { identities: [founder, readyOther], naming_diagnostic: 'naming_scan_deadline' }), { kind: 'incomplete' });
  // An older server without the naming_diagnostic field: unknown, never ready.
  assert.deepEqual(form.findCreatedAgent(sent, [], { identities: [founder, readyOther] }), { kind: 'incomplete' });
  // A roster row without naming fields and no ready identity row for it.
  const bare = { ...retainedRow, addressable_name: undefined, name_revision: undefined, naming_status: undefined };
  assert.deepEqual(form.findCreatedAgent(sent, [bare], { identities: [founder], naming_diagnostic: null }), { kind: 'incomplete' });
  // Complete observations without the name: absent (a new Create is allowed).
  assert.deepEqual(form.findCreatedAgent(sent, [], { identities: [founder, readyOther], naming_diagnostic: null }), { kind: 'absent' });
  // Positive full match is kept even when another row's naming is unavailable.
  const named = { ...retainedRow, addressable_name: 'Retained-Bot', naming_status: 'ready' };
  const otherUnavailable = { ...retainedRow, name: 'x2' };
  assert.deepEqual(
    form.findCreatedAgent(sent, [otherUnavailable, named], { identities: [founder], naming_diagnostic: 'naming_lookup_unavailable' }),
    { kind: 'matched', canonicalId: 'retained_bot' },
  );
});

// ---- F3: the transport's typed pre-forward refusals are "not sent" ----

test('F3 outcome: typed native pre-forward errors are not sent; ambiguous answers stay unknown', () => {
  const { NotSentError, NativeRecoveryError, ThrottledError, ResultUnknownError, StaleTargetError, ApiError, RemoteReadOnlyError } = client;
  assert.deepEqual(form.classifyCreateError(new NotSentError()), { kind: 'notSent', reason: 'target_changed' });
  assert.deepEqual(form.classifyCreateError(new ThrottledError()), { kind: 'notSent', reason: 'too_many_requests' });
  for (const code of ['busy', 'no_target', 'target_starting', 'target_unreachable']) {
    assert.deepEqual(form.classifyCreateError(new NativeRecoveryError({ code }, { code })), { kind: 'notSent', reason: code }, code);
  }
  assert.deepEqual(
    form.classifyCreateError(new NativeRecoveryError({ code: 'incompatible', side: 'host' }, { code: 'incompatible', side: 'host' })),
    { kind: 'notSent', reason: 'incompatible' },
  );
  // Raw 409 target_changed (not the transport's NotSentError), forwarded
  // cancellations, unrecognized 5xx and network loss: unknown.
  for (const err of [
    new NativeRecoveryError({ code: 'target_changed' }, { code: 'target_changed' }),
    new StaleTargetError(),
    new ResultUnknownError(),
    new ApiError(503, 'busy', { code: 'busy', extra: 1 }),
    new ApiError(500, null, null),
    new TypeError('Failed to fetch'),
  ]) {
    assert.deepEqual(form.classifyCreateError(err), { kind: 'unknown' }, String(err));
  }
  // The remote read-only refusal keeps its own (refused) handling.
  assert.equal(form.classifyCreateError(new RemoteReadOnlyError()).kind, 'refused');
});

// ---- F2: observations come from reads issued after retiring earlier reads ----

// gcTime Infinity schedules no GC timers, so the test process exits.
const newClient = () => new QueryClient({ defaultOptions: { queries: { gcTime: Infinity } } });

/** A QueryClient whose queryFn answers are held until released, counting calls. */
function heldQuery(qc, queryKey) {
  const calls = [];
  const observer = new QueryObserver(qc, {
    queryKey,
    retry: false,
    queryFn: ({ signal }) => new Promise((resolve, reject) => {
      calls.push({ resolve, reject, signal });
    }),
  });
  const unsubscribe = observer.subscribe(() => {});
  return { calls, unsubscribe };
}
const tick = () => new Promise((resolve) => setTimeout(resolve, 0));

test('F2 Check result: an in-flight pre-create first load is retired, not joined', async () => {
  const qc = newClient();
  const agents = heldQuery(qc, ['agents', 'acme']);
  const identities = heldQuery(qc, ['identities', 'acme']);
  await tick();
  assert.equal(agents.calls.length, 1, 'first load started before the create');
  const pending = freshReads(qc, [['agents', 'acme'], ['identities', 'acme']]);
  await tick();
  // The pre-create responses arrive late: they must not answer the check.
  agents.calls[0].resolve({ agents: ['PRE'] });
  identities.calls[0].resolve({ identities: ['PRE'] });
  await tick();
  assert.equal(agents.calls.length, 2, 'a NEW agents read was issued after the check started');
  assert.equal(identities.calls.length, 2, 'a NEW identities read was issued after the check started');
  assert.equal(agents.calls[0].signal.aborted, true, 'the earlier read was retired');
  agents.calls[1].resolve({ agents: ['POST'] });
  identities.calls[1].resolve({ identities: ['POST'] });
  assert.deepEqual(await pending, [{ agents: ['POST'] }, { identities: ['POST'] }]);
  agents.unsubscribe();
  identities.unsubscribe();
});

test('F2 Check result: a failed or missing new read is no observation (null)', async () => {
  const qc = newClient();
  const agents = heldQuery(qc, ['agents', 'acme']);
  await tick();
  agents.calls[0].resolve({ agents: ['OLD'] });
  await tick();
  const pending = freshReads(qc, [['agents', 'acme']]);
  await tick();
  agents.calls[1].reject(new Error('down'));
  assert.equal(await pending, null, 'cached OLD data never answers when the new read fails');
  assert.equal(await freshReads(qc, [['agents', 'acme'], ['identities', 'acme']]), null, 'unmounted key');
  agents.unsubscribe();
});

test('F2 success readback: in-flight first loads are retired and re-read after the create', async () => {
  const qc = newClient();
  const teams = heldQuery(qc, ['teams', 'acme']);
  await tick();
  const pending = refreshAfterMutation(qc, [['agents', 'acme'], ['teams', 'acme']]);
  await tick();
  teams.calls[0].resolve({ teams: ['PRE'] });
  await tick();
  assert.equal(teams.calls.length, 2, 'the readback issued its own teams read');
  teams.calls[1].resolve({ teams: ['POST'] });
  await pending;
  assert.deepEqual(qc.getQueryData(['teams', 'acme']), { teams: ['POST'] });
  teams.unsubscribe();
});

// ---- THR-402 v1.1 rework (Founder THR-402 seq33): N1, R1, N3, S5d ----

test('N1/R1 field refusal: value change drops it, hidden branch parks it, return re-shows it', () => {
  const values = { name: 'Insights-Lead', team: '', newTeam: 'research', executor: 'codex', description: 'd', systemPrompt: 'p' };
  const refused = { field: 'newTeam', value: 'research' };
  assert.equal(form.refusalStatus(refused, 'manager', values), 'active', 'shown next to the refused key');
  // N1: Use suggestion (or a name-driven suggestion update) changes the value.
  assert.equal(form.refusalStatus(refused, 'manager', { ...values, newTeam: 'insights' }), 'stale');
  // R1: on Worker the manager-only refusal is parked, never blocking.
  assert.equal(form.refusalStatus(refused, 'worker', { ...values, team: 'research' }), 'parked');
  // A refusal of the shared name field stays active on both branches.
  const name = { field: 'name', value: 'Insights-Lead' };
  assert.equal(form.refusalStatus(name, 'worker', values), 'active');
  assert.equal(form.refusalStatus(name, 'manager', values), 'active');
  assert.equal(form.refusalStatus(name, 'manager', { ...values, name: 'Insights-Lead2' }), 'stale');
  // Each branch submits only its own team field.
  assert.deepEqual(form.BRANCH_FIELDS.worker.filter((f) => f === 'team' || f === 'newTeam'), ['team']);
  assert.deepEqual(form.BRANCH_FIELDS.manager.filter((f) => f === 'team' || f === 'newTeam'), ['newTeam']);
});

test('N3 executor blocker: loading, read failure and confirmed none block Create; ready never does', () => {
  assert.equal(form.executorBlocker('loading'), 'executorsLoading');
  assert.equal(form.executorBlocker('error'), 'executorsError');
  assert.equal(form.executorBlocker('empty'), 'executorsNone');
  assert.equal(form.executorBlocker('ready'), null);
});

test('S5d not sent: every existing typed pre-forward reason has a named cause and next step (no invented cause)', async () => {
  const { en } = await import('../src/lib/i18n/locales/en.ts');
  const { zhCN } = await import('../src/lib/i18n/locales/zh-CN.ts');
  const reasons = ['target_changed', 'too_many_requests', 'busy', 'no_target', 'target_starting', 'target_unreachable', 'incompatible'];
  for (const reason of reasons) {
    const copy = form.notSentCopy(reason);
    for (const key of [`agents.create.notSent.${copy.cause}`, `agents.create.notSent.${copy.next}`]) {
      assert.equal(typeof en[key], 'string', `${reason}: en ${key}`);
      assert.equal(typeof zhCN[key], 'string', `${reason}: zh-CN ${key}`);
    }
  }
  // The typed classifier yields exactly these reasons for the transport's pre-forward errors.
  const recovery = new client.NativeRecoveryError({ code: 'target_unreachable' }, { detail: { code: 'target_unreachable' } });
  assert.deepEqual(form.classifyCreateError(recovery), { kind: 'notSent', reason: 'target_unreachable' });
  assert.ok(!Object.keys(en).some((k) => k.startsWith('agents.create.notSent.') && /stopping/i.test(en[k])), 'no "runtime stopping" cause');
});
