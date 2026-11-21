// Focused Node 24 checks for the THR-387 S6-W3 Connection dialog: the pure
// presentation model, the typed native Connection facade/parsers and the
// target-commit save hold. Local only (not CI, THR-291). Run from the
// repository root:
//   node --experimental-transform-types --test web/scripts/connection-dialog.test.mjs
import { registerHooks } from 'node:module';
import { existsSync } from 'node:fs';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { dirname, resolve as resolvePath } from 'node:path';
import { test } from 'node:test';
import assert from 'node:assert/strict';

// Extensionless relative and `@/` imports inside src/*.ts (same hook as native-owners.test.mjs).
const SRC = resolvePath(dirname(fileURLToPath(import.meta.url)), '..', 'src');
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

const model = await import('../src/features/connection/connectionModel.ts');
const conn = await import('../src/lib/native/connection.ts');
const target = await import('../src/lib/native/target.ts');
const owners = await import('../src/lib/native/owners.ts');

const client = (over = {}) => ({
  stage: 'idle', runtime: 'unknown', savedAccess: 'saved', route: 'unknown', observedAt: null, observation: 'known',
  computers: [{ computerRef: 'ref_1', label: 'Saved computer 2' }], activeComputerRef: null, expiresAt: null, comparisonPhrase: null, ...over,
});
const host = (over = {}) => ({
  setup: 'ready', runtime: 'available', observation: 'known', devices: [], pending: null, selected: null, inviteState: 'none', expiresAt: null, ...over,
});
const action = (over = {}) => ({
  effect: 'confirmed', hostDenial: 'unknown', hostCleanup: 'unknown', localCleanup: 'confirmed', networkCleanup: 'unknown',
  inviteState: 'unknown', expiresAt: null, host: null, client: client(), ...over,
});
const app = (over = {}) => ({
  schema: 2, generation: 'g1', target: { kind: 'local', id: 'local', name: 'This computer' }, state: 'ready', incompatible: null,
  local: { supported: true, mode: 'service', running: 'running', prompt_base_url: null }, candidate: null, access_unsaved: false,
  locale: { current: 'en', persisted: true }, ...over,
});

test('closed v1 parsers accept native shapes and reject drift', () => {
  assert.equal(conn.parseClientSnapshot(JSON.stringify(client())).stage, 'idle');
  assert.equal(conn.parseClientSnapshot(JSON.stringify({ ...client(), extra: 1 })), null, 'extra key');
  assert.equal(conn.parseClientSnapshot(JSON.stringify(client({ stage: 'ready' }))), null, 'unknown stage');
  assert.equal(conn.parseClientSnapshot(JSON.stringify(client({ computers: [{ computerRef: 'a b', label: 'x' }] }))), null, 'bad ref');
  assert.equal(conn.parseClientSnapshot(JSON.stringify(client({ computers: [{ computerRef: 'a', label: 'x‮y' }] }))), null, 'bidi label');
  assert.equal(conn.parseClientSnapshot('not json'), null);
  assert.equal(conn.parseHostSnapshot(JSON.stringify(host())).setup, 'ready');
  assert.equal(conn.parseHostSnapshot(JSON.stringify(host({ setup: 'on' }))), null);
  assert.deepEqual(Object.keys(conn.parseActionResult(JSON.stringify(action()))).sort(), ['effect', 'expiresAt', 'hostCleanup', 'hostDenial', 'inviteState', 'localCleanup', 'networkCleanup']);
  assert.equal(conn.parseActionResult(JSON.stringify(action({ effect: 'done' }))), null);
  assert.equal(conn.parseHealth(JSON.stringify({ access: 'allowed', runtime: 'available', route: 'direct', observedAt: '2026-11-21T00:00:00Z', observation: 'known' })).access, 'allowed');
  const ev = conn.parseHostEvent(JSON.stringify({ version: 1, sequence: 3, observedAt: '2026-11-21T00:00:00.000Z', snapshot: host() }));
  assert.equal(ev.sequence, 3);
  assert.equal(conn.parseHostEvent(JSON.stringify({ version: 1, sequence: 0, observedAt: '2026-11-21T00:00:00Z', snapshot: host() })), null);
});

test('refusal envelope: side only with incompatible; unreadable is unknown, never "none"', () => {
  assert.deepEqual(conn.parseConnectionFailure('{"code":"owner_save_pending","effect":"none","recovery":"refresh"}'), { code: 'owner_save_pending', effect: 'none', recovery: 'refresh' });
  assert.deepEqual(conn.parseConnectionFailure('{"code":"incompatible","effect":"none","recovery":"refresh","side":"host"}').side, 'host');
  for (const raw of ['{"code":"busy","effect":"none","recovery":"refresh","side":"host"}', 'oops', '{"code":"x"}', '{"code":"x","effect":"maybe","recovery":"refresh"}', 42]) {
    assert.deepEqual(conn.parseConnectionFailure(raw), conn.UNKNOWN_FAILURE, String(raw));
  }
  assert.equal(conn.UNKNOWN_FAILURE.effect, 'unknown');
});

test('callConnection goes through the one bridge accessor with exact call data', async () => {
  const calls = [];
  target.__setNativeBridgeForTests({
    call: async (command, data) => {
      calls.push([command, data]);
      if (command === 'client.commitSwitch') throw '{"code":"non_get_pending","effect":"none","recovery":"refresh"}';
      return '{}';
    },
    on: () => () => undefined,
  });
  await conn.callConnection('client.status');
  await conn.callConnection('client.removeSaved', { computerRef: 'ref_1' });
  await assert.rejects(conn.callConnection('client.commitSwitch'), (error) => {
    assert.ok(error instanceof conn.ConnectionRefusal);
    assert.deepEqual(conn.failureOf(error), { code: 'non_get_pending', effect: 'none', recovery: 'refresh' });
    return true;
  });
  assert.deepEqual(calls, [['client.status', '{}'], ['client.removeSaved', '{"computerRef":"ref_1"}'], ['client.commitSwitch', '{}']]);
  target.__setNativeBridgeForTests(null);
  await assert.rejects(conn.callConnection('client.status'), (error) => conn.failureOf(error).code === 'unsupportedCommand' && conn.failureOf(error).effect === 'none');
  assert.equal(conn.failureOf(new Error('x')).effect, 'unknown');
});

test('host.stateChanged: newest sequence only; a bridge without the event returns null', () => {
  let handler;
  target.__setNativeBridgeForTests({ call: async () => '{}', on: (event, fn) => { assert.equal(event, 'host.stateChanged'); handler = fn; return () => undefined; } });
  const seen = [];
  const off = conn.subscribeHostState((event) => seen.push(event.sequence));
  const raw = (sequence) => JSON.stringify({ version: 1, sequence, observedAt: '2026-11-21T00:00:00Z', snapshot: host() });
  handler(raw(2)); handler(raw(1)); handler(raw(2)); handler('bad'); handler(raw(5));
  assert.deepEqual(seen, [2, 5]);
  off();
  target.__setNativeBridgeForTests({ call: async () => '{}', on: () => { throw '{"code":"unsupportedCommand"}'; } });
  assert.equal(conn.subscribeHostState(() => undefined), null);
  target.__setNativeBridgeForTests(null);
});

test('every commitSwitch refusal maps to named copy; unknown effect is never "not switched"', () => {
  const named = {
    owner_save_pending: 'connection.refusal.ownerSavePending',
    owner_save_unknown: 'connection.refusal.ownerSaveUnknown',
    owner_edits_pending: 'connection.refusal.ownerEditsPending',
    non_get_pending: 'connection.refusal.nonGetPending',
    target_starting: 'connection.refusal.targetStarting',
    target_unreachable: 'connection.refusal.targetUnreachable',
    documentChanged: 'connection.refusal.changed',
    stateChanged: 'connection.refusal.changed',
    busy: 'connection.refusal.busy',
  };
  for (const [code, key] of Object.entries(named)) {
    const view = model.refusalView({ code, effect: 'none', recovery: 'refresh' }, 'switch');
    assert.equal(view.reason, key, code);
    assert.equal(view.lead, 'connection.refusal.notSwitched', code);
    assert.equal(view.showOwners, code.startsWith('owner_'), code);
  }
  for (const [side, key] of [['host', 'connection.refusal.incompatibleHost'], ['app', 'connection.refusal.incompatibleApp'], ['unknown', 'connection.refusal.incompatibleUnknown']]) {
    assert.equal(model.refusalView({ code: 'incompatible', effect: 'none', recovery: 'refresh', side }, 'switch').reason, key);
  }
  const unknown = model.refusalView({ code: 'outcomeUnknown', effect: 'unknown', recovery: 'nativeRecovery' }, 'switch');
  assert.equal(unknown.lead, 'connection.refusal.switchUnknown');
  assert.equal(model.refusalView({ code: 'busy', effect: 'none', recovery: 'refresh' }, 'disconnect').lead, 'connection.refusal.notDisconnected');
  assert.equal(model.refusalView({ code: 'invalidRequest', effect: 'none', recovery: 'refresh' }, 'switch').reason, 'connection.refusal.other');
});

test('action outcomes: unknown effect never says nothing changed (M9)', () => {
  const result = (effect) => ({ result: conn.parseActionResult(JSON.stringify(action({ effect }))) });
  for (const command of ['client.disconnect', 'client.cancelAttempt', 'client.removeSaved', 'host.turnOff', 'host.revokeDevice', 'host.enable']) {
    const unknown = model.outcomeView(command, result('unknown'), 'saved');
    assert.equal(unknown.lines[0].key, 'connection.client.resultUnknown', command);
    assert.equal(unknown.checkAgain, true);
    assert.equal(model.outcomeView(command, result('none'), 'saved').lines[0].key, 'connection.client.nothingChanged', command);
  }
  assert.equal(model.outcomeView('client.acceptInvite', result('none'), 'saved'), null, 'an attempt that started says nothing here');
  assert.equal(model.outcomeView('client.disconnect', result('confirmed'), 'saved').lines[0].key, 'connection.client.disconnectedSaved');
  assert.equal(model.outcomeView('client.disconnect', result('confirmed'), 'notSaved').lines[0].key, 'connection.client.disconnectedUnsaved');
  const fail = model.outcomeView('client.connectSaved', { failure: { code: 'networkUnavailable', effect: 'unknown', recovery: 'explicitRetry' } }, null);
  assert.deepEqual(fail.lines.map((l) => l.key), ['connection.client.failedAction', 'connection.err.network', 'connection.client.effectUnknown']);
  assert.equal(fail.retry, true);
  assert.equal(model.outcomeView('host.status', { failure: { code: 'stateChanged', effect: 'none', recovery: 'refresh' } }, null).lines[1].key, 'connection.host.hErrStateChanged');
  // Retry protected saving keeps its own copy, separate from the C3 save-failure copy.
  assert.equal(model.outcomeView('client.retrySave', { failure: { code: 'saveFailed', effect: 'none', recovery: 'explicitRetry' } }, null).lines[1].key, 'connection.err.saveFailed');
});

test('C6 which-side compatibility and the selected-local-stopped state', () => {
  const inc = (side, host_contract) => ({ side, host_contract, client_contract: 1, min_daemon: 1 });
  assert.equal(model.compatibilityView(inc('host', null), 'remote').body, 'connection.compat.host.noMetadata');
  assert.equal(model.compatibilityView(inc('host', { current: 0, min_client: 0 }), 'remote').body, 'connection.compat.host.older');
  assert.equal(model.compatibilityView(inc('host', { current: 0, min_client: 0 }), 'remote').title, 'connection.compat.host.title');
  assert.equal(model.compatibilityView(inc('host', null), 'local').title, 'connection.compat.localService.title');
  assert.equal(model.compatibilityView(inc('app', { current: 3, min_client: 2 }), 'remote').title, 'connection.compat.app.title');
  assert.equal(model.compatibilityView(inc('unknown', { current: 1, min_client: 1 }), 'remote').title, 'connection.compat.unknown.title');
  const stopped = model.targetView(app({ state: 'unreachable', local: { supported: true, mode: 'service', running: 'stopped', prompt_base_url: null } }));
  assert.equal(stopped.title, 'connection.target.localStopped.title');
  assert.equal(stopped.recheck, true);
  assert.equal(model.targetView(app({ state: 'no_target', target: null })).disconnect, false);
  assert.equal(model.targetView(app({ target: { kind: 'remote', id: 'r', name: 'Studio' }, local: { supported: true, mode: 'service', running: 'running', prompt_base_url: null } })).disconnect, true);
});

test('candidate is never Connected before success; Cancel while in flight or ready', () => {
  const cand = (phase) => ({ kind: 'remote', id: 'c', name: 'Studio Host', phase, deadline: null });
  for (const phase of ['signing_in', 'awaiting_approval', 'ready_to_switch', 'failed', 'cancelled']) {
    const view = model.candidateView(cand(phase), true);
    assert.ok(!/connected/i.test(view.badge) && !/connected/i.test(view.title), phase);
    assert.equal(view.cancel, ['signing_in', 'awaiting_approval', 'ready_to_switch'].includes(phase), phase);
    assert.equal(view.switchable, phase === 'ready_to_switch', phase);
    assert.equal(model.candidateCancellable(cand(phase)), view.cancel, phase);
  }
  assert.equal(model.candidateView(cand('signing_in'), true).lead, 'connection.candidate.connectingStillUsing');
  assert.equal(model.candidateView(cand('signing_in'), false).lead, 'connection.candidate.connecting');
  assert.equal(model.cancelHint('saving'), 'connection.client.cancelSafePoint');
});

test('steps: storage is done only when native reports saved access', () => {
  const rows = model.stepRows('opening', 'notSaved');
  assert.deepEqual(rows.map((r) => r.state), ['done', 'done', 'done', 'unsaved', 'current']);
  assert.equal(rows[3].note, 'connection.client.stepNotSaved');
  assert.equal(model.stepRows('opening', 'saved')[3].state, 'done');
  assert.deepEqual(model.stepRows('validating', 'saved'), []);
});

test('host view: approval, invite, unconfirmed and manage flags', () => {
  const req = model.hostView(host({ pending: { label: 'Laptop', comparisonPhrase: null } }), false);
  assert.equal(req.title, 'connection.host.hTitleRequest');
  assert.equal(req.approve, true);
  assert.equal(req.createInvite, false);
  assert.equal(model.hostView(host(), false).createInvite, true);
  assert.equal(model.hostView(host({ inviteState: 'issued' }), false).invite, 'issued');
  const uncertain = model.hostView(host(), true);
  assert.equal(uncertain.lead, 'connection.host.hLeadUnconfirmed');
  assert.equal(uncertain.manage, false);
  assert.equal(model.hostView(host({ setup: 'off' }), false).enable, true);
  assert.equal(model.displayLabel('Saved computer 12', '已保存的电脑', 'zh-CN'), '已保存的电脑 12');
  assert.equal(model.displayLabel('Studio Host', 'Saved computer', 'en'), 'Studio Host');
});

test('a target-commit hold admits no save until released', async () => {
  target.__setNativeBridgeForTests({ call: async () => '{}', on: () => () => undefined });
  owners.__resetNativeOwnersForTests();
  const records = [];
  assert.equal(await owners.awaitOwnersReceipt(records), true);
  const release = owners.holdOwnerSaves();
  assert.equal(owners.ownersFrozen(), true);
  assert.equal(await owners.awaitOwnersReceipt(records), false);
  release();
  release(); // idempotent
  assert.equal(owners.ownersFrozen(), false);
  assert.equal(await owners.awaitOwnersReceipt(records), true);
  target.__setNativeBridgeForTests(null);
});
