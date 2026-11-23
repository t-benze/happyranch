// Portable execution of the shipping facade and the existing shared save gate.
import { registerHooks } from 'node:module';
import { readFileSync, existsSync } from 'node:fs';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { dirname, resolve } from 'node:path';
import vm from 'node:vm';
import assert from 'node:assert/strict';

const root = resolve(dirname(fileURLToPath(import.meta.url)), '../..');
registerHooks({ resolve(specifier, context, next) {
  if (specifier.startsWith('.') && context.parentURL?.endsWith('.ts')) {
    const path = resolve(dirname(fileURLToPath(context.parentURL)), specifier) + '.ts';
    if (existsSync(path)) return next(pathToFileURL(path).href, context);
  }
  return next(specifier, context);
} });
const source = readFileSync(resolve(root, 'app/mac/Sources/HappyRanchApp/AppSchemeScripts.swift'), 'utf8');
const page = source.split('static let page = #"""\n')[1].split('\n"""#')[0];
const diagnostics = source.split('static let diagnostics = #"""\n')[1]?.split('\n"""#')[0];
const posts = [];
let receipt;
const window = {
  fetch() { throw Error('Unexpected network'); },
  webkit: { messageHandlers: { appTransport: { postMessage(item) {
    posts.push(item);
    return new Promise(resolve => { receipt = resolve; });
  } } } },
};
const context = '11'.repeat(16);
const sandbox = { window, top: window, location: { origin: 'happyranch-app://app', protocol:'happyranch-app:', host:'app' },
  crypto: { getRandomValues: bytes => bytes.fill(0x11) }, document: { readyState:'complete' }, Map, Object, Uint8Array };
vm.runInNewContext(page, sandbox);
assert.equal(window.__hrAppContext, context, 'shipping relay must create a document context');
assert.equal(window.__hrAppBootstrap?.stage, 'complete', 'shipping relay must mark successful bootstrap');
const readFacts = environment => vm.runInNewContext(`(() => {${diagnostics}})()`, environment);
assert.equal(readFacts(sandbox).bootstrap, 'complete');
assert.equal(readFacts(sandbox).origin, 'app');
assert.ok(!JSON.stringify(readFacts(sandbox)).includes(context), 'diagnostics must never contain the context token');
for (const [label, expectedStage, throws] of [
  ['not-injected', 'missing', false],
  ['opaque-origin', 'refused-origin', false],
  ['foreign-origin', 'refused-origin', false],
  ['subframe', 'refused-top-frame', false],
  ['missing-webkit', 'native-handler', true],
  ['throwing-handler', 'native-handler', true],
  ['missing-crypto', 'context-random', true],
  ['context-collision', 'context-define', true],
]) {
  const probe = { fetch() {}, webkit: { messageHandlers: { appTransport: { postMessage() {} } } }, isSecureContext:false };
  const environment = { window:probe, top:probe,
    location:{ origin:'happyranch-app://app', protocol:'happyranch-app:', host:'app' },
    crypto:{ getRandomValues:bytes => bytes.fill(0x11) }, document:{ readyState:'complete' } };
  if (label === 'opaque-origin') environment.location.origin = 'null';
  if (label === 'foreign-origin') environment.location = { origin:'https://foreign.invalid', protocol:'https:', host:'foreign.invalid' };
  if (label === 'subframe') environment.top = {};
  if (label === 'missing-webkit') delete probe.webkit;
  if (label === 'throwing-handler') Object.defineProperty(probe.webkit.messageHandlers, 'appTransport', {
    get() { throw new TypeError('synthetic handler failure'); },
  });
  if (label === 'missing-crypto') delete environment.crypto;
  if (label === 'context-collision') Object.defineProperty(probe, '__hrAppContext', { value:false });
  if (label !== 'not-injected') {
    if (throws) assert.throws(() => vm.runInNewContext(page, environment), undefined, label);
    else vm.runInNewContext(page, environment);
  }
  const facts = readFacts(environment);
  assert.equal(facts.bootstrap, expectedStage, label);
  assert.equal(facts.origin, label === 'opaque-origin' ? 'null' : label === 'foreign-origin' ? 'other' : 'app', label);
  assert.equal(facts.top, label === 'subframe' ? 'false' : 'true', label);
  assert.equal(facts.secure, 'false', label);
  assert.equal(facts.appTransport, label === 'missing-webkit' ? 'undefined' : label === 'throwing-handler' ? 'threw' : 'object', label);
  assert.notEqual(typeof probe.__hrAppContext, 'string', `${label}: failure must not gain a valid context`);
  assert.ok(!JSON.stringify(facts).includes(context), `${label}: diagnostic token exclusion`);
}
assert.deepEqual(Object.keys(window.happyranchConnection).sort(), ['call', 'on'], 'public API stays unchanged');
const target = await import(pathToFileURL(resolve(root, 'web/src/lib/native/target.ts')));
target.__setNativeBridgeForTests(window.happyranchConnection);
const { createOwnerRegistry } = await import(pathToFileURL(resolve(root, 'web/src/lib/owners/ownerRegistry.ts')));
const { awaitOwnersReceipt } = await import(pathToFileURL(resolve(root, 'web/src/lib/native/owners.ts')));
const registry = createOwnerRegistry();
registry.register('work-hours', { scope: 'main', page: 'settings.workHours', org: 'synthetic', computer: null });
registry.setDraft('work-hours', { dirty: true, revision: 1 });
const ticket = registry.beginSave('work-hours');
assert.ok(ticket);
let settled = false;
const admission = awaitOwnersReceipt(registry.getSnapshot()).then(value => { settled = true; return value; });
await new Promise(resolve => setImmediate(resolve));
assert.equal(posts.length, 0, 'owner push must wait for native document admission');
window.__hrAppAdmit('foreign-document');
await new Promise(resolve => setImmediate(resolve));
assert.equal(posts.length, 0, 'another document cannot release this relay');
window.__hrAppAdmit(context);
await new Promise(resolve => setImmediate(resolve));
assert.equal(posts.length, 1, 'pending owner summary must reach native');
assert.equal(posts[0].command, 'app.ownersChanged');
assert.equal(posts[0].context, context, 'native envelope captures the sending document');
assert.deepEqual(JSON.parse(posts[0].data), {
  schema: 2, blocking: 'hold', counts: { dirty: 0, failed: 0, pending: 1, unknown: 0 },
});
assert.equal(settled, false, 'save must wait for native receipt');
receipt('{}');
assert.equal(await admission, true, 'registered save must admit after native receipt');
registry.settleSave(ticket, 'unknown');
// Receipt loss/refusal must continue to hold saves (fresh snapshot avoids dedupe).
registry.setDraft('work-hours', { dirty: true, revision: 2 });
const refused = awaitOwnersReceipt(registry.getSnapshot());
await new Promise(resolve => setImmediate(resolve));
receipt('invalid receipt');
assert.equal(await refused, false);
window.__hrAppAdmit('replacement-document');
const late = window.happyranchConnection.call('app.ownersChanged', posts[0].data);
await new Promise(resolve => setImmediate(resolve));
assert.equal(posts.at(-1).context, context, 'outgoing relay cannot acquire a replacement context');
receipt('{}');
await late;
console.log('PASS: shipped bootstrap diagnostics distinguish missing/refused/throwing stages without tokens; document-bound owner push, admission and receipt gates');

// Execute the commissioned qualifier itself. Simulated upstream failures must
// not prevent the later independent steps; this does not emulate WebKit EOF.
const qualifierSource = readFileSync(resolve(root, 'app/mac/Sources/HappyRanchApp/AppSchemeQualification.swift'), 'utf8');
const qualifier = qualifierSource.split('private static let page = #"""\n')[1].split('\n"""#')[0];
for (const fault of ['stream-eof', 'none', 'parse', 'emit', 'abort-read', 'download-fetch', 'download-save']) {
  const events = [], calls = [], aborted = [];
  const win = { happyranchConnection:{ call:async () => '{"generation":"synthetic"}' },
    webkit:{ messageHandlers:{ macQualification:{ postMessage(item) {
      if (fault === 'emit' && item.event === 'stream_end') throw Error('synthetic-private-value');
      events.push(item);
    } } } } };
  const document = { getElementById:() => ({childElementCount:1}), body:{append() {}}, createElement:() => ({
    click() { queueMicrotask(() => win.__hrQualificationDownload?.(fault !== 'download-save')); }, remove() {},
  }) };
  const fetch = async (path, options = {}) => {
    const mode = path.split('/').at(-1); calls.push(mode);
    if (mode === 'echo') return Response.json({bytes:20, intact:true});
    if (mode === 'upload') return Response.json({fileBytes:10485760, intact:true});
    if (mode === 'download') {
      if (fault === 'download-fetch') return new Promise(() => {});
      return new Response('mac1 download\n');
    }
    options.signal.addEventListener('abort', () => aborted.push(mode), {once:true});
    return new Response(new ReadableStream({start(controller) {
      if (fault === 'abort-read' && mode === 'abort') return;
      const count = mode === 'stream' ? 5 : 2;
      for (let index = 0; index < count; index++) controller.enqueue(new TextEncoder().encode(
        fault === 'parse' && mode === 'stream' ? 'data: invalid\n\n' : `data: ${JSON.stringify({index,sentWallMs:Date.now()})}\n\n`));
      if (!(fault === 'stream-eof' && mode === 'stream')) controller.close();
    } }), {headers:{'Content-Type':'text/event-stream'}});
  };
  const environment = {window:win, top:win, document, fetch, location:{origin:'happyranch-app://app',pathname:'/onboarding'},
    sessionStorage:{getItem:() => '1'}, Blob, FormData, Response, TextDecoder, TextEncoder, Uint8Array, AbortController, Date,
    URL:{createObjectURL:() => 'blob:synthetic', revokeObjectURL() {}},
    setTimeout:(callback, milliseconds) => setTimeout(callback, Math.min(milliseconds, 30)), clearTimeout};
  let watchdog;
  try {
    await Promise.race([vm.runInNewContext(qualifier, environment), new Promise((_, reject) => {
      watchdog = setTimeout(() => reject(Error(`${fault}: independent steps did not finish`)), 300);
    })]);
  } finally { clearTimeout(watchdog); }
  assert.deepEqual(calls.slice(-3), ['stream','abort','download'], `${fault}: every independent step must run`);
  const steps = events.filter(item => item.event === 'step');
  assert.deepEqual(steps.map(item => item.step), ['stream','abort','download'], `${fault}: one result per step`);
  const failedStep = ['stream-eof','parse','emit'].includes(fault) ? 'stream' : fault === 'abort-read' ? 'abort'
    : fault.startsWith('download-') ? 'download' : null;
  for (const step of steps) assert.equal(step.status, step.step === failedStep ? 'FAIL' : 'PASS', `${fault}/${step.step}`);
  assert.ok(events.some(item => item.event === 'done'), `${fault}: terminal page receipt`);
  if (failedStep) {
    const diagnostic = events.find(item => item.event === 'step_diagnostic' && item.step === failedStep);
    assert.ok(diagnostic, `${fault}: closed failure diagnostics`);
    assert.ok(!JSON.stringify(diagnostic).includes('synthetic-private-value'));
    if (fault === 'stream-eof') {
      assert.equal(diagnostic.lastPageStep, 'read-pending');
      assert.equal(diagnostic.framesReceived, 5);
      assert.ok(diagnostic.bytesReceived > 0);
    }
    if (fault === 'emit') assert.equal(diagnostic.lastPageStep, 'end-emit');
  } else assert.ok(!events.some(item => item.event === 'step_diagnostic'), 'successful runs emit no failure diagnostics');
  assert.ok(aborted.includes('abort'), `${fault}: explicit abort reaches the fetch signal`);
}
console.log('PASS: qualifier bounds EOF/parse/emit/abort/download failures, continues every step and emits closed failure facts');
