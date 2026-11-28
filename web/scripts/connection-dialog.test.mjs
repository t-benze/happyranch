// Focused Node 24 checks for the THR-387 S6-W3 Connection dialog: the pure
// presentation model, the typed native Connection facade/parsers and the
// target-commit save hold. Local only (not CI, THR-291). Run from the
// repository root:
//   node --experimental-transform-types --test web/scripts/connection-dialog.test.mjs
import { registerHooks } from 'node:module';
import { existsSync, readFileSync } from 'node:fs';
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

if (process.env.ENABLE_PREPARATION_TRACE) test('Production Enable trace reaches Connection model', async () => {
  const trace = JSON.parse(readFileSync(process.env.ENABLE_PREPARATION_TRACE, 'utf8'));
  assert.equal(trace.child.stdout, '{"preparationFailure":"inventory"}\n');
  assert.equal(trace.signal.destination, ':1.42');
  const event = conn.parseEnableFinished(trace.relayEvent);
  assert.equal(event.failure.category, 'inventory');
  assert.equal(event.failure.effect, 'unknown');
  const en = (await import('../src/lib/i18n/locales/en.ts')).default;
  const zh = (await import('../src/lib/i18n/locales/zh-CN.ts')).default;
  const view = model.outcomeView('host.enable', { failure: event.failure }, null);
  assert.equal(en[view.lines[1].key], 'The home network routes could not be verified.');
  assert.equal(zh[view.lines[1].key], '无法验证家庭网络路由。');
});

const preparation = [
  ['deb-layout-required', 'This installation does not support turning on remote access.', '当前安装方式不支持开启远程访问。'],
  ['issuer-state-uncertain', 'The device invitation service could not be prepared safely.', '无法安全准备设备邀请服务。'],
  ['account-shape', 'The service account does not match the required setup.', '服务账户不符合所需配置。'],
  ['account-receipt-uncertain', 'The service account ownership record could not be verified.', '无法验证服务账户的所有权记录。'],
  ['foreign-unit', 'An existing service configuration is not owned by HappyRanch.', '现有服务配置不属于 HappyRanch。'],
  ['foreign-custody', 'Existing remote access files are not owned by HappyRanch.', '现有远程访问文件不属于 HappyRanch。'],
  ['publication-uncertain', 'The remote access configuration could not be verified.', '无法验证远程访问配置。'],
  ['address-changed', 'The home network address has changed since setup.', '家庭网络地址在设置后发生了变化。'],
  ['account-create-uncertain', 'The service account could not be created and verified.', '无法创建并验证服务账户。'],
  ['cleanup-uncertain', 'Existing remote access services could not be confirmed stopped.', '无法确认现有远程访问服务已停止。'],
  ['materializer-uncertain', 'The secure certificate setup could not be verified.', '无法验证安全证书配置。'],
  ['custody-uncertain', 'Safe ownership of remote access files could not be verified.', '无法验证远程访问文件的安全所有权。'],
  ['inventory', 'The home network routes could not be verified.', '无法验证家庭网络路由。'],
  ['no_address', 'No usable home network address was found.', '未找到可用的家庭网络地址。'],
  ['multiple_addresses', 'More than one home network address was found.', '找到多个家庭网络地址。'],
  ['dial_ip', 'The home network address is not supported.', '不支持此家庭网络地址。'],
  ['dial-ip', 'The home network address is not supported.', '不支持此家庭网络地址。'],
  ['occupied-port', 'A network port needed for remote access is already in use.', '远程访问所需的网络端口已被占用。'],
  ['port-check-uncertain', 'The network ports needed for remote access could not be checked.', '无法检查远程访问所需的网络端口。'],
  ['ready-restart-failed-turn-off', 'The owned services could not restart or verify. Turn off remote access, then check its result.', '所属服务无法重新启动或通过验证。请关闭远程访问，然后检查操作结果。'],
  ['activation-retry-later', 'The previous single-use key is still live. Wait for its original expiry, then try Turn on again.', '上一次的一次性密钥仍在有效期内。请等待其原定到期时间，然后再次开启。'],
  ['activation-prepared-retry', 'Recovery prepared a fresh attempt. Choose Turn on again to finish setup.', '恢复已准备好新的尝试。请再次开启以完成设置。'],
  ['activation-unverified', 'Setup could not be verified. Check current status before another action; Turn off may report incomplete cleanup.', '无法验证设置结果。执行其他操作前请检查当前状态；关闭操作可能报告清理未完成。'],
];
for (const [category, english, chinese] of preparation) {
  test('Preparation token retains literal bilingual reason: ' + category, async () => {
    const en = (await import('../src/lib/i18n/locales/en.ts')).default;
    const zh = (await import('../src/lib/i18n/locales/zh-CN.ts')).default;
    const input = { code: 'enablePreparationFailed', category, effect: 'unknown', recovery: 'nativeRecovery' };
    const failure = conn.parseConnectionFailure(JSON.stringify(input));
    assert.deepEqual(failure, input);
    const view = model.outcomeView('host.enable', { failure }, null);
    assert.equal(en[view.lines[0].key], 'Turn on failed.');
    assert.equal(zh[view.lines[0].key], '开启失败。');
    assert.equal(en[view.lines[1].key], english);
    assert.equal(zh[view.lines[1].key], chinese);
    assert.equal(en[view.lines[2].key], 'Its effect is unknown.');
    assert.equal(zh[view.lines[2].key], '其影响未知。');
    assert.equal(view.retry, false);
    assert.equal(view.checkAgain, true);
    const { HostView } = await import('../connection/src/host.ts');
    const { catalogs } = await import('../connection/src/strings.ts');
    for (const [locale, reason] of [['en', english], ['zh-CN', chinese]]) {
      const receiver = { outcome: { command: 'host.enable', raw: null, error: failure },
        t: (key) => catalogs[locale][key],
        notice: (text) => ({ textContent: text }), text: (_tag, text) => ({ textContent: text }),
        has: () => false, actions: () => [] };
      const notices = HostView.prototype.outcomeNodes.call(receiver);
      assert.ok(notices[0].textContent.includes(reason), locale + ': ' + notices[0].textContent);
      assert.ok(notices[0].textContent.includes(catalogs[locale].effectUnknown));
    }
    for (const snapshot of [host({ setup: 'off' }), host(), host({ observation: 'unavailable' })]) {
      assert.equal(model.hostView(snapshot, snapshot.observation !== 'known').enable,
        snapshot.observation === 'known' && snapshot.setup === 'off');
      assert.equal(failure.effect, 'unknown');
    }
  });
}

test('Enable projection rejects invalid diagnostic shapes', () => {
  const good = { code: 'enablePreparationFailed', category: 'inventory', effect: 'unknown', recovery: 'nativeRecovery' };
  for (const wrong of [
    { category: 'unknown' }, { category: null }, { category: ' inventory' }, { code: 'unavailable' },
    { effect: 'none' }, { effect: 'confirmed' }, { recovery: 'explicitRetry' }, { message: 'SYNTHETIC_PRIVATE_CANARY' },
  ]) assert.deepEqual(conn.parseConnectionFailure(JSON.stringify({ ...good, ...wrong })), conn.UNKNOWN_FAILURE);
  const { category: ignored, ...missing } = good;
  assert.deepEqual(conn.parseConnectionFailure(JSON.stringify(missing)), conn.UNKNOWN_FAILURE);
  const admission = action({ effect: 'unknown', hostDenial: 'unknown', hostCleanup: 'unknown',
    localCleanup: 'notApplicable', networkCleanup: 'unknown', client: null });
  assert.equal(conn.parseEnableAdmission(JSON.stringify(admission)).operationId, undefined);
  const operationId = 'a'.repeat(32);
  assert.equal(conn.parseEnableAdmission(JSON.stringify({ ...admission, operationId })).operationId, operationId);
  assert.equal(conn.parseActionResult(JSON.stringify({ ...admission, operationId })), null);
  for (const wrong of [{ effect: 'confirmed' }, { operationId: 'A'.repeat(32) }, { extra: 1 }])
    assert.equal(conn.parseEnableAdmission(JSON.stringify({ ...admission, operationId, ...wrong })), null);
  const event = { version: 1, method: 'Enable', operationId, outcome: 'failed', error: good };
  assert.deepEqual(conn.parseEnableFinished(JSON.stringify(event)), { operationId, outcome: 'failed', failure: good });
  for (const wrong of [{ error: missing }, { extra: 1 }, { method: 'Disable' }, { outcome: 'completed' },
    { result: { code: 'enableCompleted', effect: 'unknown', recovery: 'refresh' } }])
    assert.equal(conn.parseEnableFinished(JSON.stringify({ ...event, ...wrong })), null);
  const completed = { code: 'enableCompleted', effect: 'unknown', recovery: 'refresh' };
  assert.deepEqual(conn.parseConnectionFailure(JSON.stringify(completed)), conn.UNKNOWN_FAILURE);
  assert.equal(conn.parseEnableFinished(JSON.stringify({ version: 1, method: 'Enable', operationId,
    outcome: 'completed', result: completed })).outcome, 'completed');
});

test('Enable subscription retires queued delivery without replay or mutation', () => {
  const callbacks = [], calls = [], seen = [];
  target.__setNativeBridgeForTests({ call: (...args) => { calls.push(args); return Promise.resolve('{}'); },
    on: (event, handler) => { assert.equal(event, 'host.enableFinished'); callbacks.push(handler); return () => {}; } });
  const event = JSON.stringify({ version: 1, operationId: 'a'.repeat(32), method: 'Enable', outcome: 'failed',
    error: { code: 'enablePreparationFailed', category: 'inventory', effect: 'unknown', recovery: 'nativeRecovery' } });
  const off = conn.subscribeEnableFinished((value) => seen.push(value));
  callbacks[0](event);
  off();
  callbacks[0](event);
  assert.equal(seen.length, 1);
  const next = [];
  conn.subscribeEnableFinished((value) => next.push(value))();
  callbacks[0](event);
  assert.deepEqual(next, []);
  assert.deepEqual(calls, []);
  target.__setNativeBridgeForTests(null);
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

test('host view: approval, invite, unconfirmed and manage flags', async () => {
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
  assert.equal(model.errorKey('host.status', 'hostStateUnrecognized'), 'connection.host.hErrStateUnrecognized');
  assert.equal(model.displayLabel('Saved computer 12', '已保存的电脑', 'zh-CN'), '已保存的电脑 12');
  assert.equal(model.displayLabel('Studio Host', 'Saved computer', 'en'), 'Studio Host');
  const { HostView } = await import('../connection/src/host.ts');
  const { catalogs } = await import('../connection/src/strings.ts');
  for (const [setup, enable, turnOff, checkAgain] of [
    ['off', true, false, false], ['pending', false, false, true],
    ['ready', false, true, false], ['paused', false, true, true],
    ['failed', false, false, true], ['unknown', false, false, true],
    ['prepared_no_receipt', true, false, true], ['failed_uncertain', true, true, true],
    ['partial_cleanup', false, true, true], ['runtime_unavailable', true, true, true],
    ['setup_unverified', false, true, true], ['renewal_failed', false, true, true],
  ]) {
    const snapshot = host({ setup });
    const view = model.hostView(snapshot, false);
    const actual = { main: [view.enable, view.turnOff, view.checkAgain] };
    // Exercise the shipped standalone body, including its private action list.
    // Nodes isolate the DOM; production chooses every command and label.
    for (const locale of ['en', 'zh-CN']) {
      const receiver = { snapshot, unconfirmed: false, confirm: null,
        pendingLabel: () => null, limited: () => false,
        outcomeNodes: () => [], confirmation: () => [], invite: () => [], devices: () => [],
        t: (key) => catalogs[locale][key],
        text: (_tag, textContent) => ({ textContent }),
        actions: (nodes) => nodes.filter(Boolean), has: () => true,
        gate: (_command, create) => create(),
        button: (command, key) => ({ command, textContent: catalogs[locale][key], dataset: { focusKey: `${command}:${key}` } }),
      };
      const { nodes } = HostView.prototype.body.call(receiver, setup);
      actual[locale] = ['host.enable', 'host.turnOff', 'host.status'].map((command) =>
        nodes.some((node) => node.command === command));
      const button = nodes.find((node) => node.command === 'host.turnOff');
      if (button) assert.equal(button.textContent, catalogs[locale].hTurnOff);
    }
    const expected = [enable, turnOff, checkAgain];
    assert.deepEqual(actual, { main: expected, en: expected, 'zh-CN': expected }, setup);
  }
});

test('host state categories survive parsing and select bilingual GUI copy', async () => {
  const en = (await import('../src/lib/i18n/locales/en.ts')).default;
  const zh = (await import('../src/lib/i18n/locales/zh-CN.ts')).default;
  for (const category of ['residue', 'owner', 'provenance', 'custody', 'inventory', 'quiescence', 'timeout', 'status_unavailable', 'invite_rate_limited', 'unknown', 'unverified', 'not_applicable', 'turn_off_then_on']) {
    const input = { code: 'hostStateUnrecognized', effect: 'none', recovery: 'refresh', category };
    const failure = conn.parseConnectionFailure(JSON.stringify(input));
    assert.deepEqual(failure, { ...input, category: category === 'unknown' ? 'unverified' : category });
    const view = model.outcomeView('host.status', { failure }, null);
    const key = view.lines[1].key;
    assert.notEqual(key, 'connection.host.hErrStateUnrecognized');
    assert.equal(typeof en[key], 'string');
    assert.equal(typeof zh[key], 'string');
    assert.notEqual(en[key], zh[key]);
    if (category === 'turn_off_then_on') {
      assert.equal(en[key], 'This home network was set up by an older version. Reinstall the previous version, Turn off there, then install this version and Turn on.');
      assert.equal(zh[key], '此家庭网络由旧版本设置。请重新安装之前的版本，在旧版本中关闭家庭网络，然后安装此版本并开启。');
    }
    const { HostView } = await import('../connection/src/host.ts');
    const { catalogs } = await import('../connection/src/strings.ts');
    for (const [locale, reason] of [['en', en[key]], ['zh-CN', zh[key]]]) {
      const receiver = { outcome: { command: 'host.status', raw: null, error: failure },
        t: (name) => catalogs[locale][name], notice: (text) => ({ textContent: text }),
        has: () => false, actions: () => [] };
      assert.ok(HostView.prototype.outcomeNodes.call(receiver)[0].textContent.includes(reason));
    }
    assert.equal(view.checkAgain, true);
    assert.equal(view.retry, false);
  }
  for (const category of ['private/path', 7, {}, '']) {
    const failure = conn.parseConnectionFailure(JSON.stringify({ code: 'hostStateUnrecognized', effect: 'none', recovery: 'refresh', category }));
    assert.equal(failure.category, 'unverified');
  }
  assert.deepEqual(conn.parseConnectionFailure(JSON.stringify({ code: 'unavailable', effect: 'none', recovery: 'refresh', category: 'owner' })), conn.UNKNOWN_FAILURE);
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


test('host firewall advice preserves READY and renders bilingual LAN-only action', async () => {
  const { HostView } = await import('../connection/src/host.ts');
  const { catalogs } = await import('../connection/src/strings.ts');
  for (const category of ['likely_blocked', 'unverified', 'clear']) {
    const snapshot = host({ firewallWarning: category });
    assert.deepEqual(conn.parseHostSnapshot(JSON.stringify(snapshot)), snapshot);
    const view = model.hostView(snapshot, false);
    assert.equal(view.badge, 'connection.host.hBadgeReady');
    const key = category === 'likely_blocked' ? 'firewallLikelyBlocked' : 'firewallUnverified';
    assert.equal(view.hints.includes('connection.host.' + key), category !== 'clear');
    assert.deepEqual(conn.parseEnableFinished(JSON.stringify({ version: 1, operationId: 'a'.repeat(32),
      method: 'Enable', outcome: 'completed', result: {code: 'enableCompleted', effect: 'unknown', recovery: 'refresh', firewallWarning: category} })),
      {operationId: 'a'.repeat(32), outcome: 'completed', firewallWarning: category});
    if (category === 'clear') continue;
    for (const locale of ['en', 'zh-CN']) {
      const reason = catalogs[locale][key];
      assert.match(reason, locale === 'en' ? /home LAN only/ : /仅允许家庭局域网/);
      assert.match(reason, /28443\/tcp/);
      assert.match(reason, /23478\/udp/);
      if (category === 'likely_blocked') assert.match(reason, locale === 'en' ? /probably/ : /可能/);
      const receiver = { snapshot, unconfirmed: false, confirm: null,
        pendingLabel: () => null, limited: () => false,
        outcomeNodes: () => [], confirmation: () => [], invite: () => [], devices: () => [],
        t: (name) => catalogs[locale][name], text: (_tag, textContent) => ({textContent}),
        notice: (textContent) => ({textContent}), actions: (nodes) => nodes.filter(Boolean),
        has: () => false, gate: (_command, create) => create(), button: () => ({dataset: {}}) };
      const {nodes} = HostView.prototype.body.call(receiver, 'ready');
      assert.ok(nodes.some(node => node.textContent === reason));
    }
  }
  assert.equal(conn.parseHostSnapshot(JSON.stringify(host({ firewallWarning: '/private/rule' }))), null);
  assert.deepEqual(conn.parseHostSnapshot(JSON.stringify(host())), host());
});
