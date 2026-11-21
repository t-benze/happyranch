// Focused Node 24 checks for THR-387 S8-1-W (web native locale authority):
// the `app.locale.set` request bytes and reply mapping (Saved only for a
// matching persisted:true acknowledgement; persisted:false is session-only;
// refusal / lost / malformed / other-locale replies fail and re-read the
// native snapshot without re-sending the setter), the native adapter's
// snapshot and report subscription (never touching browser storage), the pure
// report reconciler (newer choice wins, no rollback, late Saved after a lost
// reply), adapter selection, and kind-based built-in name localization.
// Request/reply fixtures are validated against
// `protocol/native-connection-bridge.v2.json` `appLocaleSet` /
// `appLocaleSetResult` (ajv 6 from web/node_modules, `$defs` rewritten to
// `definitions`; local evidence only, not wired into CI).
// Run from the repository root:
//   node --experimental-transform-types --test web/scripts/native-locale.test.mjs
import { registerHooks, createRequire } from 'node:module';
import { existsSync, readFileSync } from 'node:fs';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { dirname, resolve as resolvePath } from 'node:path';
import { test, mock } from 'node:test';
import assert from 'node:assert/strict';

const HERE = dirname(fileURLToPath(import.meta.url));
const SRC = resolvePath(HERE, '..', 'src');
const ROOT = resolvePath(HERE, '..', '..');

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

// Any browser-storage access in native mode is a defect: record it.
const storageTouches = [];
Object.defineProperty(globalThis, 'localStorage', {
  configurable: true,
  get() {
    storageTouches.push('localStorage');
    throw new Error('native locale must not read browser storage');
  },
});

// Each module is loaded softly, so a base tree without the S8-1-W exports
// reports every intended check as its own failure instead of one load error.
const soft = (path) => import(path).catch((error) => ({ __loadError: error }));
const targetMod = await soft('../src/lib/native/target.ts');
const localeMod = await soft('../src/lib/native/locale.ts');
const appStatusMod = await soft('../src/lib/native/appStatus.ts');
const i18nMod = await soft('../src/lib/i18n/locale.ts');
function need(mod, name) {
  const value = mod[name];
  assert.ok(value !== undefined, `missing export ${name}${mod.__loadError ? ` (${mod.__loadError.code ?? mod.__loadError.message})` : ''}`);
  return value;
}
const __setNativeBridgeForTests = (next) => need(targetMod, '__setNativeBridgeForTests')(next);
const LOCALE_SET_COMMAND = 'app.locale.set';
const appLocalePreferenceAdapter = (...a) => need(localeMod, 'appLocalePreferenceAdapter')(...a);
const localeSetRequest = (...a) => need(localeMod, 'localeSetRequest')(...a);
const parseLocaleSetReply = (...a) => need(localeMod, 'parseLocaleSetReply')(...a);
const setNativeLocale = (...a) => need(localeMod, 'setNativeLocale')(...a);
const nativeAdapter = () => need(localeMod, 'nativeLocalePreferenceAdapter');
const appEntryName = (...a) => need(appStatusMod, 'appEntryName')(...a);
const browserAdapter = () => need(i18nMod, 'browserLocalePreferenceAdapter');
const reconcileNativeReport = (...a) => need(i18nMod, 'reconcileNativeReport')(...a);

// ---- schema (merged protocol) ----
const require = createRequire(import.meta.url);
const Ajv = require('../node_modules/ajv');
const schema = JSON.parse(
  readFileSync(resolvePath(ROOT, 'protocol/native-connection-bridge.v2.json'), 'utf8').replaceAll('#/$defs/', '#/definitions/'),
);
schema.definitions = schema.$defs;
delete schema.$defs;
delete schema.$schema;
const ajv = new Ajv({ allErrors: true });
ajv.addSchema(schema, 'bridge');
const validSet = ajv.getSchema('bridge#/definitions/appLocaleSet');
const validResult = ajv.getSchema('bridge#/definitions/appLocaleSetResult');

// ---- fakes ----
function appState(current, persisted, generation = 'g1') {
  return JSON.stringify({
    schema: 2,
    generation,
    target: { kind: 'local', id: 'local', name: 'This computer' },
    state: 'ready',
    incompatible: null,
    local: { supported: true, mode: 'service', running: 'running', prompt_base_url: null },
    candidate: null,
    access_unsaved: false,
    locale: { current, persisted },
  });
}

/** Fake bridge; `onSet(data)` answers `app.locale.set`, `status()` answers `app.status`. */
function fakeBridge({ onSet = () => Promise.resolve('{}'), status = () => appState('en', true) } = {}) {
  const calls = [];
  const listeners = new Map();
  const bridge = {
    call(command, data) {
      calls.push({ command, data });
      if (command === 'app.status') return Promise.resolve(status());
      if (command === LOCALE_SET_COMMAND) return onSet(data);
      return Promise.reject(JSON.stringify({ code: 'unsupportedCommand', effect: 'none', recovery: 'none' }));
    },
    on(event, handler) {
      listeners.set(event, handler);
      return () => listeners.delete(event);
    },
  };
  return {
    bridge,
    calls,
    sets: () => calls.filter((c) => c.command === LOCALE_SET_COMMAND),
    statusReads: () => calls.filter((c) => c.command === 'app.status').length,
    emit: (event, raw) => listeners.get(event)?.(raw),
  };
}

const reply = (current, persisted) => JSON.stringify({ schema: 2, locale: { current, persisted } });
const flush = () => new Promise((resolve) => setImmediate(resolve));

test('app.locale.set request is exactly the closed appLocaleSet value', async () => {
  const fake = fakeBridge({ onSet: () => Promise.resolve(reply('zh-CN', true)) });
  __setNativeBridgeForTests(fake.bridge);
  await setNativeLocale('zh-CN');
  assert.deepEqual(fake.sets(), [{ command: 'app.locale.set', data: '{"schema":2,"locale":"zh-CN"}' }]);
  for (const locale of ['en', 'zh-CN']) {
    assert.ok(validSet(JSON.parse(localeSetRequest(locale))), JSON.stringify(validSet.errors));
  }
});

test('Saved only for a matching persisted:true reply; persisted:false is session-only', async () => {
  for (const [persisted, expected] of [[true, { status: 'durable' }], [false, { status: 'session-only' }]]) {
    const raw = reply('zh-CN', persisted);
    assert.ok(validResult(JSON.parse(raw)), JSON.stringify(validResult.errors));
    const fake = fakeBridge({ onSet: () => Promise.resolve(raw) });
    __setNativeBridgeForTests(fake.bridge);
    assert.deepEqual(await setNativeLocale('zh-CN'), expected);
    await flush();
    assert.equal(fake.statusReads(), 0, 'a usable reply needs no snapshot re-read');
  }
});

test('mismatched, malformed or off-schema replies fail and re-read status without re-sending the setter', async () => {
  const bad = [
    reply('en', true), // another locale than requested
    JSON.stringify({ schema: 2, locale: { current: 'zh-CN' } }),
    JSON.stringify({ schema: 2, locale: { current: 'zh-CN', persisted: true, path: 'synthetic' } }),
    JSON.stringify({ schema: 1, locale: { current: 'zh-CN', persisted: true } }),
    JSON.stringify({ schema: 2, locale: { current: 'zh-CN', persisted: 'true' } }),
    '{}',
    'not json',
  ];
  for (const raw of bad) {
    const fake = fakeBridge({ onSet: () => Promise.resolve(raw) });
    __setNativeBridgeForTests(fake.bridge);
    assert.deepEqual(await setNativeLocale('zh-CN'), { status: 'failed', reason: 'error' }, raw);
    await flush();
    assert.equal(fake.sets().length, 1, `setter sent once: ${raw}`);
    assert.equal(fake.statusReads(), 1, `snapshot re-read: ${raw}`);
  }
  assert.deepEqual(parseLocaleSetReply(reply('zh-CN', true), 'en'), { status: 'failed', reason: 'error' });
});

test('refusal fails as rejected; no bridge fails as native-unavailable', async () => {
  const fake = fakeBridge({
    onSet: () => Promise.reject(JSON.stringify({ code: 'invalidRequest', effect: 'none', recovery: 'none' })),
  });
  __setNativeBridgeForTests(fake.bridge);
  assert.deepEqual(await setNativeLocale('en'), { status: 'failed', reason: 'rejected' });
  __setNativeBridgeForTests(null);
  assert.deepEqual(await setNativeLocale('en'), { status: 'failed', reason: 'native-unavailable' });
});

test('no reply within the status timeout fails, re-reads status once, and a late reply changes nothing', async () => {
  mock.timers.enable({ apis: ['setTimeout'] });
  try {
    let answer;
    const fake = fakeBridge({ onSet: () => new Promise((resolve) => { answer = resolve; }) });
    __setNativeBridgeForTests(fake.bridge);
    let outcome = null;
    void setNativeLocale('zh-CN').then((value) => { outcome = value; });
    mock.timers.tick(4999);
    await flush();
    assert.equal(outcome, null, 'still pending before the timeout');
    mock.timers.tick(1);
    await flush();
    assert.deepEqual(outcome, { status: 'failed', reason: 'error' });
    assert.equal(fake.statusReads(), 1);
    answer(reply('zh-CN', true));
    await flush();
    assert.deepEqual(outcome, { status: 'failed', reason: 'error' }, 'late ack is dropped');
    assert.equal(fake.sets().length, 1, 'setter never re-sent');
  } finally {
    mock.timers.reset();
  }
});

test('native adapter: authority native, snapshot from app.status, reports from stateChanged, no browser storage', async () => {
  storageTouches.length = 0;
  const fake = fakeBridge({ status: () => appState('zh-CN', false) });
  __setNativeBridgeForTests(fake.bridge);
  assert.equal(nativeAdapter().authority, 'native');
  assert.equal(nativeAdapter().readSnapshot().native, undefined, 'nothing known before app.status');
  const reports = [];
  const off = nativeAdapter().subscribe((report) => reports.push(report));
  await flush();
  assert.deepEqual(reports, [{ current: 'zh-CN', persisted: false }], 'first app.status delivered');
  const snap = nativeAdapter().readSnapshot();
  assert.equal(snap.saved, null);
  assert.equal(snap.native, 'zh-CN');
  assert.equal(snap.persisted, false);
  fake.emit('app.stateChanged', appState('en', true, 'g1'));
  assert.deepEqual(reports.at(-1), { current: 'en', persisted: true });
  // A report already known at subscribe time is delivered once immediately.
  const late = [];
  const offLate = nativeAdapter().subscribe((report) => late.push(report));
  assert.deepEqual(late, [{ current: 'en', persisted: true }]);
  off();
  offLate();
  fake.emit('app.stateChanged', appState('zh-CN', true, 'g1'));
  assert.equal(reports.length, 2, 'unsubscribed listener is silent');
  await nativeAdapter().write('en');
  assert.deepEqual(storageTouches, [], 'browser storage never touched in native mode');
});

test('adapter selection: browser adapter without the bridge, native adapter behind it', () => {
  __setNativeBridgeForTests(null);
  assert.equal(appLocalePreferenceAdapter(), browserAdapter());
  __setNativeBridgeForTests(fakeBridge().bridge);
  assert.equal(appLocalePreferenceAdapter(), nativeAdapter());
  __setNativeBridgeForTests(null);
});

test('report reconciler: adopt before a choice, ignore while pending or for another locale, late Saved after a lost reply', () => {
  const view = (choice, pending, persistence) => ({ choice, pending, persistence });
  const r = (current, persisted) => ({ current, persisted });
  // Before any local choice: the late first app.status / startup / reopen.
  assert.deepEqual(reconcileNativeReport(view(null, false, 'idle'), r('zh-CN', true)), { kind: 'adopt', locale: 'zh-CN', persistence: 'durable' });
  assert.deepEqual(reconcileNativeReport(view(null, false, 'idle'), r('en', false)), { kind: 'adopt', locale: 'en', persistence: 'session-only' });
  // Unsupported native value never applies.
  assert.deepEqual(reconcileNativeReport(view(null, false, 'idle'), r('fr', true)), { kind: 'ignore' });
  // A newer choice is pending: no older snapshot restores copy or shows Saved.
  assert.deepEqual(reconcileNativeReport(view('en', true, 'pending'), r('zh-CN', true)), { kind: 'ignore' });
  assert.deepEqual(reconcileNativeReport(view('en', true, 'pending'), r('en', true)), { kind: 'ignore' });
  // Another locale than the latest choice: no rollback of the live choice.
  assert.deepEqual(reconcileNativeReport(view('zh-CN', false, 'failed'), r('en', true)), { kind: 'ignore' });
  assert.deepEqual(reconcileNativeReport(view('zh-CN', false, 'session-only'), r('en', false)), { kind: 'ignore' });
  // Lost/late reply, then native reports the latest choice saved: Saved.
  assert.deepEqual(reconcileNativeReport(view('zh-CN', false, 'failed'), r('zh-CN', true)), { kind: 'settle', persistence: 'durable' });
  // Not saved stays failed; session-only and Saved are not changed by echoes.
  assert.deepEqual(reconcileNativeReport(view('zh-CN', false, 'failed'), r('zh-CN', false)), { kind: 'ignore' });
  assert.deepEqual(reconcileNativeReport(view('zh-CN', false, 'session-only'), r('zh-CN', true)), { kind: 'ignore' });
  assert.deepEqual(reconcileNativeReport(view('zh-CN', false, 'durable'), r('zh-CN', false)), { kind: 'ignore' });
});

test('built-in local name follows the live locale by kind; remote names stay verbatim', () => {
  assert.equal(appEntryName({ kind: 'local', name: 'This computer' }, '此电脑'), '此电脑');
  assert.equal(appEntryName({ kind: 'local', name: '这台电脑' }, 'This computer'), 'This computer');
  assert.equal(appEntryName({ kind: 'remote', name: 'This computer' }, '此电脑'), 'This computer');
  assert.equal(appEntryName({ kind: 'remote', name: 'Studio <Host>' }, '此电脑'), 'Studio <Host>');
});
