// Focused Node 24 checks for THR-387 S6-W2b (web owner summary push to native:
// `app.ownersChanged` payloads, receipt-gated save admission, serialization,
// dedupe against the last acknowledged summary, `app.lifecycleRequested`
// validation and once-only keep/proceed answers). Every captured payload is
// validated against `protocol/native-connection-bridge.v2.json`
// `#/$defs/ownersChanged` (ajv 6 from web/node_modules, `$defs` rewritten to
// `definitions`; local evidence only, not wired into CI).
// Run from the repository root:
//   node --experimental-transform-types --test web/scripts/native-owners.test.mjs
import { registerHooks, createRequire } from 'node:module';
import { existsSync, readFileSync } from 'node:fs';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { dirname, resolve as resolvePath } from 'node:path';
import { test, mock } from 'node:test';
import assert from 'node:assert/strict';

const HERE = dirname(fileURLToPath(import.meta.url));
const SRC = resolvePath(HERE, '..', 'src');
const ROOT = resolvePath(HERE, '..', '..');

// Resolve `@/` and extensionless relative imports from .ts modules, including
// modules imported with a `?case=N` query (a fresh owners module per test).
registerHooks({
  resolve(specifier, context, nextResolve) {
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
});

const { __setNativeBridgeForTests } = await import('../src/lib/native/target.ts');
const { createOwnerRegistry } = await import('../src/lib/owners/ownerRegistry.ts');

// ---- schema (merged protocol) ----
const require = createRequire(import.meta.url);
const Ajv = require('../node_modules/ajv');
const schemaText = readFileSync(resolvePath(ROOT, 'protocol/native-connection-bridge.v2.json'), 'utf8')
  .replaceAll('#/$defs/', '#/definitions/');
const schema = JSON.parse(schemaText);
schema.definitions = schema.$defs;
delete schema.$defs;
delete schema.$schema;
const ajv = new Ajv({ allErrors: true });
ajv.addSchema(schema, 'bridge');
const validOwners = ajv.getSchema('bridge#/definitions/ownersChanged');
const validLifecycle = ajv.getSchema('bridge#/definitions/lifecycleRequested');
function assertValidOwners(data) {
  const value = JSON.parse(data);
  assert.ok(validOwners(value), `ownersChanged schema: ${data} ${JSON.stringify(validOwners.errors)}`);
  return value;
}

// ---- fakes ----
let caseNo = 0;
async function freshOwners() {
  caseNo += 1;
  return import(`../src/lib/native/owners.ts?case=${caseNo}`);
}

/** Fake native bridge: records calls; `reply(i)` controls each call's answer. */
function fakeBridge({ reply = () => Promise.resolve('{}'), onThrows = false } = {}) {
  const calls = [];
  const listeners = new Map();
  let inFlight = 0;
  let maxInFlight = 0;
  const bridge = {
    call(command, data) {
      calls.push({ command, data });
      inFlight += 1;
      maxInFlight = Math.max(maxInFlight, inFlight);
      const answer = reply(calls.length - 1, command, data);
      return Promise.resolve(answer).finally(() => {
        inFlight -= 1;
      });
    },
    on(event, handler) {
      if (onThrows) throw JSON.stringify({ code: 'unsupportedCommand', effect: 'none', recovery: 'refresh' });
      listeners.set(event, handler);
      return () => listeners.delete(event);
    },
  };
  return {
    bridge,
    calls,
    emit: (event, raw) => listeners.get(event)?.(raw),
    owners: () => calls.filter((c) => c.command === 'app.ownersChanged').map((c) => assertValidOwners(c.data)),
    get maxInFlight() {
      return maxInFlight;
    },
  };
}

const REFUSED = JSON.stringify({ code: 'unsupportedCommand', effect: 'none', recovery: 'refresh' });
const LOST = JSON.stringify({ code: 'documentChanged', effect: 'unknown', recovery: 'nativeRecovery' });
const SETTINGS = { scope: 'settings', page: 'settings.organization', org: 'studio', computer: null, section: 'organization' };
const MAIN = { scope: 'main', page: 'agents.agent', subject: 'dev', org: 'studio', computer: null };
const OTHER_ORG = { scope: 'main', page: 'agents.agent', subject: 'ops', org: 'acme', computer: null };
const flush = () => new Promise((resolve) => setImmediate(resolve));
const counts = (dirty, failed, pending, unknown) => ({ dirty, failed, pending, unknown });

test('summary per owner state matches the closed v2 schema (clean/dirty/failed/pending/unknown/confirmed/mixed/saturated)', async () => {
  const { toOwnersSummary, serializeOwnersSummary } = await freshOwners();
  const r = createOwnerRegistry();
  const snap = () => JSON.parse(serializeOwnersSummary(toOwnersSummary(r.getSnapshot())));
  const check = (expected) => {
    const value = snap();
    assert.ok(validOwners(value), JSON.stringify(validOwners.errors));
    assert.deepEqual(value, expected);
  };
  check({ schema: 2, blocking: 'none', counts: counts(0, 0, 0, 0) }); // no owners
  r.register('a', SETTINGS);
  check({ schema: 2, blocking: 'none', counts: counts(0, 0, 0, 0) }); // clean
  r.setDraft('a', { dirty: true, revision: 1 });
  check({ schema: 2, blocking: 'prompt', counts: counts(1, 0, 0, 0) }); // dirty
  const t1 = r.beginSave('a');
  check({ schema: 2, blocking: 'hold', counts: counts(0, 0, 1, 0) }); // pending
  r.settleSave(t1, 'failed');
  check({ schema: 2, blocking: 'prompt', counts: counts(0, 1, 0, 0) }); // failed
  const t2 = r.beginSave('a');
  r.settleSave(t2, 'unknown');
  check({ schema: 2, blocking: 'hold', counts: counts(0, 0, 0, 1) }); // unknown
  r.reconcile('a', 'saved');
  check({ schema: 2, blocking: 'none', counts: counts(0, 0, 0, 0) }); // confirmed
  assert.equal(r.getSnapshot()[0].status, 'confirmed');
  r.register('m', MAIN);
  r.setDraft('m', { dirty: true, revision: 1 });
  r.register('p', SETTINGS);
  r.setDraft('p', { dirty: true, revision: 1 });
  r.beginSave('p');
  check({ schema: 2, blocking: 'hold', counts: counts(1, 0, 1, 0) }); // mixed Main dirty + Settings pending
  const big = createOwnerRegistry();
  for (let i = 0; i < 300; i += 1) {
    big.register(`o${i}`, MAIN);
    big.setDraft(`o${i}`, { dirty: true, revision: 1 });
  }
  big.register('x', SETTINGS);
  big.setDraft('x', { dirty: true, revision: 1 });
  big.settleSave(big.beginSave('x'), 'unknown');
  const sat = JSON.parse(serializeOwnersSummary(toOwnersSummary(big.getSnapshot())));
  assert.ok(validOwners(sat));
  assert.deepEqual(sat, { schema: 2, blocking: 'hold', counts: counts(255, 0, 0, 1) }); // saturated, unknown kept
});

test('C1: a newer draft survives an older success (hold -> prompt, never none); payload has no page/org/ids', async () => {
  const owners = await freshOwners();
  const fake = fakeBridge();
  __setNativeBridgeForTests(fake.bridge);
  const r = createOwnerRegistry();
  r.subscribe(() => void owners.publishOwners(r.getSnapshot()));
  r.register('a', SETTINGS);
  r.setDraft('a', { dirty: true, revision: 1 });
  const ticket = r.beginSave('a');
  r.setDraft('a', { dirty: true, revision: 2 }); // edit during the save
  r.settleSave(ticket, 'saved');
  await flush();
  const blocking = fake.owners().map((s) => s.blocking);
  assert.deepEqual(blocking, ['none', 'prompt', 'hold', 'prompt']);
  for (const { data } of fake.calls) assert.doesNotMatch(data, /studio|organization|settings|agents|"a"/);
});

test('C2/MM-8: org change keeps a detached old-org pending owner counting; the wire has no org field', async () => {
  const owners = await freshOwners();
  const fake = fakeBridge();
  __setNativeBridgeForTests(fake.bridge);
  const r = createOwnerRegistry();
  r.subscribe(() => void owners.publishOwners(r.getSnapshot()));
  r.register('old', OTHER_ORG);
  r.setDraft('old', { dirty: true, revision: 1 });
  const ticket = r.beginSave('old');
  r.release('old'); // org switch unmounts the acme editor; its sent save stays
  r.register('new', MAIN); // studio editor mounts, clean
  await flush();
  const last = fake.owners().at(-1);
  assert.deepEqual(last, { schema: 2, blocking: 'hold', counts: counts(0, 0, 1, 0) });
  r.settleSave(ticket, 'saved');
  await flush();
  assert.deepEqual(fake.owners().at(-1), { schema: 2, blocking: 'none', counts: counts(0, 0, 0, 0) });
  for (const { data } of fake.calls) assert.doesNotMatch(data, /acme|studio/);
});

test('browser mode (no bridge): no calls, saves admitted at once, lifecycle subscription is a no-op', async () => {
  const owners = await freshOwners();
  __setNativeBridgeForTests(null);
  const r = createOwnerRegistry();
  r.register('a', SETTINGS);
  assert.equal(await owners.publishOwners(r.getSnapshot()), 'acked');
  assert.equal(await owners.awaitOwnersReceipt(r.getSnapshot()), true);
  assert.equal(owners.answerLifecycle('id1', 'proceed', () => r.getSnapshot()), null);
  const off = owners.subscribeLifecycle(() => assert.fail('no events in browser mode'));
  off();
  assert.equal(owners.ownersFrozen(), false);
});

test('serialized one-in-flight, observation order; dedupe against the LAST ACKNOWLEDGED summary only', async () => {
  const owners = await freshOwners();
  const answers = ['{}', Promise.reject(REFUSED), '{}', '{}'];
  const fake = fakeBridge({
    reply: (i) => new Promise((resolve, reject) => setTimeout(() => Promise.resolve(answers[i] ?? '{}').then(resolve, reject), 5)),
  });
  answers[1].catch(() => undefined);
  __setNativeBridgeForTests(fake.bridge);
  const r = createOwnerRegistry();
  const none = r.getSnapshot();
  const p1 = owners.publishOwners(none); // sent, acked
  const p2 = owners.publishOwners(none); // identical to acked: not sent
  r.register('a', SETTINGS);
  r.setDraft('a', { dirty: true, revision: 1 });
  const p3 = owners.publishOwners(r.getSnapshot()); // prompt: sent, refused
  const p4 = owners.publishOwners(r.getSnapshot()); // identical but last push failed: sent again
  const p5 = owners.publishOwners(r.getSnapshot()); // now acked: not sent
  assert.deepEqual(await Promise.all([p1, p2, p3, p4, p5]), ['acked', 'acked', 'refused', 'acked', 'acked']);
  assert.deepEqual(fake.owners().map((s) => s.blocking), ['none', 'prompt', 'prompt']);
  assert.equal(fake.maxInFlight, 1);
});

test('save admission: waits for the pending summary receipt; refused, lost and timed-out pushes hold the save', async () => {
  for (const [label, answer, expected] of [
    ['acked', () => '{}', true],
    ['refused', () => Promise.reject(REFUSED), false],
    ['lost (documentChanged effect unknown)', () => Promise.reject(LOST), false],
    ['non-receipt reply', () => '{"x":1}', false],
  ]) {
    const owners = await freshOwners();
    const fake = fakeBridge({ reply: answer });
    __setNativeBridgeForTests(fake.bridge);
    const r = createOwnerRegistry();
    r.subscribe(() => void owners.publishOwners(r.getSnapshot()));
    r.register('a', SETTINGS);
    r.setDraft('a', { dirty: true, revision: 1 });
    r.beginSave('a'); // the hold summary is queued synchronously
    assert.equal(await owners.awaitOwnersReceipt(r.getSnapshot()), expected, label);
    // register (none), dirty (prompt), begin (hold): the receipt shares the queued hold push.
    assert.equal(fake.calls.length, 3, `${label}: no extra call for the receipt`);
    assert.equal(fake.owners().at(-1).blocking, 'hold', label);
  }
  // No reply at all: lost after RECEIPT_TIMEOUT_MS.
  const owners = await freshOwners();
  mock.timers.enable({ apis: ['setTimeout'] });
  try {
    const fake = fakeBridge({ reply: () => new Promise(() => undefined) });
    __setNativeBridgeForTests(fake.bridge);
    const r = createOwnerRegistry();
    r.register('a', SETTINGS);
    r.setDraft('a', { dirty: true, revision: 1 });
    r.beginSave('a');
    const admitted = owners.awaitOwnersReceipt(r.getSnapshot());
    await flush();
    assert.equal(fake.calls.length, 1);
    mock.timers.tick(owners.RECEIPT_TIMEOUT_MS);
    assert.equal(await admitted, false);
  } finally {
    mock.timers.reset();
  }
});

test('lifecycle: invalid events ignored; on() that throws is tolerated; repeats flagged; each ID answered once', async () => {
  const owners = await freshOwners();
  for (const raw of [
    '{"schema":2,"requestId":"q1","action":"quit"}',
    '{"schema":2,"requestId":"r_1-A","action":"relaunch"}',
    '{"schema":2,"requestId":"c","action":"close"}',
  ]) assert.ok(validLifecycle(JSON.parse(raw)) && owners.parseLifecycleRequested(raw), raw);
  for (const raw of [
    '{"schema":2,"requestId":"q1","action":"quit","extra":1}',
    '{"schema":1,"requestId":"q1","action":"quit"}',
    '{"schema":2,"requestId":"bad id","action":"quit"}',
    `{"schema":2,"requestId":"${'x'.repeat(65)}","action":"quit"}`,
    '{"schema":2,"requestId":"","action":"quit"}',
    '{"schema":2,"requestId":"q1","action":"restart"}',
    '{"schema":2,"requestId":"q1"}',
    '[1]',
    'not json',
    42,
  ]) {
    assert.equal(owners.parseLifecycleRequested(raw), null, String(raw));
    if (typeof raw === 'string' && raw.startsWith('{')) assert.equal(validLifecycle(JSON.parse(raw)), false, raw);
  }

  const throwing = fakeBridge({ onThrows: true });
  __setNativeBridgeForTests(throwing.bridge);
  const offThrowing = owners.subscribeLifecycle(() => assert.fail('never'));
  offThrowing();

  const fake = fakeBridge();
  __setNativeBridgeForTests(fake.bridge);
  const seen = [];
  owners.subscribeLifecycle((request) => seen.push(request));
  fake.emit('app.lifecycleRequested', '{"schema":2,"requestId":"q1","action":"quit","x":0}');
  fake.emit('app.lifecycleRequested', '{"schema":2,"requestId":"q1","action":"quit"}');
  fake.emit('app.lifecycleRequested', '{"schema":2,"requestId":"q1","action":"quit"}');
  assert.deepEqual(seen, [
    { requestId: 'q1', action: 'quit', repeat: false },
    { requestId: 'q1', action: 'quit', repeat: true },
  ]);
  const r = createOwnerRegistry();
  assert.ok(owners.answerLifecycle('q1', 'keep', () => r.getSnapshot()));
  assert.equal(owners.answerLifecycle('q1', 'proceed', () => r.getSnapshot()), null);
  await flush();
  fake.emit('app.lifecycleRequested', '{"schema":2,"requestId":"q1","action":"quit"}');
  assert.equal(seen.length, 2, 'answered ID is ignored afterwards');
  const sent = fake.owners();
  assert.equal(sent.length, 1);
  assert.deepEqual(sent[0], { schema: 2, blocking: 'none', counts: counts(0, 0, 0, 0), gate: { requestId: 'q1', decision: 'keep' } });
});

test('decisions: proceed only while none (else keep); proceed freezes saves; refusal unfreezes; lost ack keeps the hold', async () => {
  // Blocking at send time -> keep, not frozen.
  let owners = await freshOwners();
  let fake = fakeBridge();
  __setNativeBridgeForTests(fake.bridge);
  const r = createOwnerRegistry();
  r.register('a', SETTINGS);
  r.setDraft('a', { dirty: true, revision: 1 });
  assert.equal(await owners.answerLifecycle('p1', 'proceed', () => r.getSnapshot()), 'acked');
  assert.deepEqual(fake.owners().at(-1), { schema: 2, blocking: 'prompt', counts: counts(1, 0, 0, 0), gate: { requestId: 'p1', decision: 'keep' } });
  assert.equal(owners.ownersFrozen(), false);

  // None -> proceed; acked keeps the freeze: no new save is admitted.
  r.discard('a');
  assert.equal(await owners.answerLifecycle('p2', 'proceed', () => r.getSnapshot()), 'acked');
  assert.deepEqual(fake.owners().at(-1), { schema: 2, blocking: 'none', counts: counts(0, 0, 0, 0), gate: { requestId: 'p2', decision: 'proceed' } });
  assert.equal(owners.ownersFrozen(), true);
  r.setDraft('a', { dirty: true, revision: 2 });
  r.beginSave('a');
  const callsBefore = fake.calls.length;
  assert.equal(await owners.awaitOwnersReceipt(r.getSnapshot()), false);
  assert.equal(fake.calls.length, callsBefore, 'frozen: nothing pushed for the held save either');

  // Definitive refusal of proceed restores saving.
  owners = await freshOwners();
  fake = fakeBridge({ reply: (i, command, data) => (data.includes('"gate"') ? Promise.reject(REFUSED) : '{}') });
  __setNativeBridgeForTests(fake.bridge);
  const clean = createOwnerRegistry();
  assert.equal(await owners.answerLifecycle('p3', 'proceed', () => clean.getSnapshot()), 'refused');
  await flush();
  assert.equal(owners.ownersFrozen(), false);

  // Lost acknowledgement keeps the hold.
  owners = await freshOwners();
  fake = fakeBridge({ reply: () => Promise.reject(LOST) });
  __setNativeBridgeForTests(fake.bridge);
  assert.equal(await owners.answerLifecycle('p4', 'proceed', () => clean.getSnapshot()), 'lost');
  await flush();
  assert.equal(owners.ownersFrozen(), true);
});

test('reload: a fresh document module sends its own initial summary and never replays an old gate answer', async () => {
  const fake = fakeBridge();
  __setNativeBridgeForTests(fake.bridge);
  const first = await freshOwners();
  const r = createOwnerRegistry();
  first.subscribeLifecycle(() => undefined);
  fake.emit('app.lifecycleRequested', '{"schema":2,"requestId":"old","action":"close"}');
  await first.publishOwners(r.getSnapshot());
  // Reload: new document, new module state, nothing answered or acknowledged.
  __setNativeBridgeForTests(fake.bridge);
  const second = await freshOwners();
  await second.publishOwners(createOwnerRegistry().getSnapshot());
  const sent = fake.owners();
  assert.equal(sent.length, 2, 'identical initial summary is sent again by the new document');
  assert.ok(sent.every((s) => s.gate === undefined), 'no gate answer for the old document ID');
});

// ---- S6-W2b: agent system prompt save (SystemPromptEditor) ----
const { saveAndVerifySystemPrompt } = await import('../src/features/agents/systemPromptSave.ts');
const { classifySaveError } = await import('../src/lib/owners/ownerRegistry.ts');
const PROMPT_OWNER = { scope: 'main', page: 'agents.systemPrompt', subject: 'dev', org: 'studio', computer: null };
const REV_B = 'b'.repeat(64);
const PROMPT_RECEIPT = { agent: 'dev', system_prompt: 'new prompt', revision: REV_B };
const validRevision = (value) => typeof value === 'string' && /^[0-9a-f]{64}$/.test(value);

/** The editor's submit flow: begin, then the production helper with the owner's `sendable()`. */
async function runPromptSave({ reply, read, puts = [] }) {
  const owners = await freshOwners();
  const fake = fakeBridge({ reply });
  __setNativeBridgeForTests(fake.bridge);
  const r = createOwnerRegistry();
  r.subscribe(() => void owners.publishOwners(r.getSnapshot()));
  r.register('prompt', PROMPT_OWNER);
  r.setDraft('prompt', { dirty: true, revision: 1 });
  const ticket = r.beginSave('prompt');
  const done = await saveAndVerifySystemPrompt({
    agentName: 'dev',
    save: async () => {
      // What native had acknowledged when the PUT was sent.
      puts.push({ callsBefore: fake.calls.length });
      return PROMPT_RECEIPT;
    },
    read,
    classifySaveError,
    validRevision,
    sendable: () => owners.awaitOwnersReceipt(r.getSnapshot()),
  });
  r.settleSave(ticket, done.outcome);
  const status = r.getSnapshot().find((o) => o.id === 'prompt')?.status;
  return { done, puts, status, fake };
}

test('system prompt save: no PUT until native acknowledged the pending summary; refused/lost holds it (failed, nothing sent)', async () => {
  const matching = async () => ({ system_prompt: 'new prompt', revision: REV_B });
  // Receipt withheld, then acknowledged: the PUT starts only after the hold ack.
  let release;
  const gate = new Promise((resolve) => { release = resolve; });
  const withheld = [];
  const pending = runPromptSave({ reply: (i) => (i === 2 ? gate.then(() => '{}') : '{}'), read: matching, puts: withheld });
  for (let i = 0; i < 5; i += 1) await flush();
  assert.equal(withheld.length, 0, 'no PUT while the receipt is withheld');
  release();
  const acked = await pending;
  assert.equal(acked.puts.length, 1, 'one PUT after the ack');
  assert.equal(acked.fake.owners()[2].blocking, 'hold', 'the acknowledged summary showed the save pending');
  assert.ok(acked.puts[0].callsBefore >= 3, 'PUT sent after the hold summary call');
  assert.equal(acked.done.outcome, 'saved');
  assert.equal(acked.status, 'confirmed');

  for (const [label, reply] of [
    ['refused', (i) => (i === 2 ? Promise.reject(REFUSED) : '{}')],
    ['lost', (i) => (i === 2 ? Promise.reject(LOST) : '{}')],
  ]) {
    const run = await runPromptSave({ reply, read: matching });
    assert.equal(run.puts.length, 0, `${label}: nothing sent`);
    assert.equal(run.done.outcome, 'failed', label);
    assert.deepEqual(run.done.result, { kind: 'held' }, label);
    assert.equal(run.status, 'failed', `${label}: ticket settled failed`);
  }
});

test('system prompt save: #1775 kept — an acknowledged save whose verification read fails or disagrees stays held (unknown)', async () => {
  for (const [label, read] of [
    ['read fails 503', async () => { throw Object.assign(new Error('unavailable'), { status: 503 }); }],
    ['read disagrees', async () => ({ system_prompt: 'other client', revision: 'c'.repeat(64) })],
  ]) {
    const run = await runPromptSave({ reply: () => '{}', read });
    assert.equal(run.puts.length, 1, `${label}: sent after the receipt`);
    assert.equal(run.done.outcome, 'unknown', label);
    assert.equal(run.status, 'unknown', `${label}: held, not saved`);
  }
});

test('W07 close/Quit summaries recheck remaining owners after keyless acknowledgement', async () => {
  const owners = await freshOwners();
  const fake = fakeBridge();
  __setNativeBridgeForTests(fake.bridge);
  const r = createOwnerRegistry();
  for (const id of ['keyless', 'other']) {
    r.register(id, SETTINGS);
    r.setDraft(id, { dirty: true, revision: 1 });
  }
  r.setUnknownAcknowledgement('keyless', true);
  const ticket = r.beginSave('keyless');
  const other = r.beginSave('other');
  r.settleSave(ticket, 'unknown');
  r.settleSave(other, 'unknown');
  assert.equal(owners.toOwnersSummary(r.getSnapshot()).counts.unknown, 2);
  assert.equal(r.acknowledgeUnknown('keyless'), false);
  const check = r.beginUnknownCheck('keyless');
  assert.equal(r.completeUnknownCheck(check), true);
  assert.equal(r.acknowledgeUnknown('keyless'), true);
  let summary = owners.toOwnersSummary(r.getSnapshot());
  assert.deepEqual(summary, { schema: 2, blocking: 'hold', counts: { dirty: 0, failed: 0, pending: 0, unknown: 1 } });
  assertValidOwners(owners.serializeOwnersSummary(summary));
  assert.equal(await owners.answerLifecycle('ack-close-held', 'proceed', () => r.getSnapshot()), 'acked');
  assert.equal(fake.owners().at(-1).gate.decision, 'keep', 'another owner still blocks close');
  r.settleSave(ticket, 'saved');
  assert.deepEqual(owners.toOwnersSummary(r.getSnapshot()), summary, 'late own success has no saved claim');
  r.reconcile('other', 'saved');
  summary = owners.toOwnersSummary(r.getSnapshot());
  assert.equal(summary.blocking, 'none');
  assert.equal(summary.counts.unknown, 0);
  assertValidOwners(owners.serializeOwnersSummary(summary));
  assert.equal(await owners.answerLifecycle('ack-quit-clear', 'proceed', () => r.getSnapshot()), 'acked');
  assert.equal(fake.owners().at(-1).gate.decision, 'proceed', 'only an actually clear aggregate permits Quit');
});
