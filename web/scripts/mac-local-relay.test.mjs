// Execute the shipped Main relay, rather than reimplementing its dispatcher.
// node --experimental-transform-types --test web/scripts/mac-local-relay.test.mjs
import { readFileSync } from 'node:fs';
import { runInNewContext } from 'node:vm';
import { webcrypto } from 'node:crypto';
import { test } from 'node:test';
import assert from 'node:assert/strict';
const source = readFileSync(new URL('../../app/mac/Sources/HappyRanchApp/AppSchemeScripts.swift', import.meta.url), 'utf8');
const script = source.split('static let page = #"""')[1].split('"""#')[0];
function relay(postMessage) {
  const page = { crypto: webcrypto, location: { origin: 'happyranch-app://app' },
    fetch: async () => { throw new Error('unexpected fetch'); }, addEventListener() {},
    Request, Response, URL, ReadableStream, Uint8Array, DOMException, setTimeout, clearTimeout,
    webkit: { messageHandlers: { appTransport: { postMessage } } } };
  page.window = page; page.top = page;
  runInNewContext(script, page);
  return page;
}
for (const kind of ['string', 'Error']) {
  test(`Mac local relay exposes the closed ${kind} refusal string without retry`, async () => {
    const refusal = JSON.stringify({ code: 'busy', effect: 'none', recovery: 'refresh' });
    const posts = [];
    const page = relay(async item => { posts.push(item); throw kind === 'string' ? refusal : new Error(refusal); });
    const pending = page.happyranchConnection.call('local.restart', '{}');
    await Promise.resolve();
    assert.equal(posts.length, 0, 'no dispatch before Main admission');
    page.__hrAppAdmit(page.__hrAppContext);
    await assert.rejects(pending, error => error === refusal);
    assert.equal(posts.length, 1);
    assert.equal(posts[0].command, 'local.restart');
    assert.equal(posts[0].data, '{}');
  });
}
test('Mac local relay keeps exactly the fixed local command set and rejects non-string data before dispatch', async () => {
  const posts = [];
  const page = relay(async item => { posts.push(item); return '{}'; });
  page.__hrAppAdmit(page.__hrAppContext);
  for (const command of ['local.status', 'local.start', 'local.stop', 'local.restart', 'local.update.status', 'local.keepRunning.set', 'local.repair', 'local.turnOff']) {
    assert.equal(await page.happyranchConnection.call(command, '{}'), '{}');
  }
  assert.equal(posts.length, 8);
  await assert.rejects(page.happyranchConnection.call('local.start', {}), error => JSON.parse(error).code === 'invalidRequest');
  await assert.rejects(page.happyranchConnection.call('local.execute', '{}'));
  assert.equal(posts.length, 8, 'neither malformed data nor an unknown name can enter native');
});

// Same VM venue, executing the actual qualifier script with test-side geometry.
const qualification = readFileSync(new URL('../../app/mac/Sources/HappyRanchApp/AppSchemeQualification.swift', import.meta.url), 'utf8');
const scrollPlan = qualification.split('static let dialogScrollPlan = #"""')[1].split('"""#')[0];
function pane({ top = 80, bottom = 520, height = 440, content = 1800, overflow = 'auto', parent = null } = {}) {
  return { clientHeight: height, scrollHeight: content, scrollTop: 20, parentElement: parent,
    style: { overflowY: overflow, visibility: 'visible', display: 'block' },
    getBoundingClientRect: () => ({ top, bottom, left: 0, right: 760, width: 760 }) };
}
function plan(dialog, background = []) {
  const page = { innerHeight: 560, innerWidth: 760, window: {},
    getComputedStyle: e => e.style,
    document: { scrollingElement: background[0] ?? null,
      querySelector: () => dialog, querySelectorAll: () => [...background, dialog, ...dialog.children] } };
  const result = runInNewContext(`(function () { ${scrollPlan} })()`, page);
  return { result: JSON.parse(JSON.stringify(result)), scrollers: Array.from(page.window.__hrCaptureScrollers) };
}
test('Mac capture at 760x560 / 200% plans the active Settings panes and excludes background overflow', () => {
  const dialog = pane({ height: 560, content: 560, top: 0, bottom: 560, overflow: 'hidden' });
  const settings = pane({ parent: dialog });
  dialog.children = [settings]; dialog.querySelectorAll = () => dialog.children;
  const background = pane({ top: 559, bottom: 2000, height: 1441, content: 10000 });
  const { result, scrollers } = plan(dialog, [background]);
  assert.deepEqual(scrollers, [settings]);
  assert.deepEqual(result, [{ index: 0, step: 330, maximum: 1360, pages: 5 }]);
});
test('Mac capture excludes panes clipped by hidden ancestors instead of exhausting its page budget', () => {
  const dialog = pane({ height: 560, content: 560, top: 0, bottom: 560, overflow: 'hidden' });
  const hidden = pane({ top: 600, bottom: 900, overflow: 'hidden', parent: dialog });
  const clipped = pane({ parent: hidden });
  const settings = pane({ parent: dialog });
  dialog.children = [hidden, clipped, settings]; dialog.querySelectorAll = () => dialog.children;
  const { result, scrollers } = plan(dialog);
  assert.deepEqual(scrollers, [settings]);
  assert.equal(result.reduce((n, p) => n + p.pages, 0), 5);
});

test('Mac locale relay preserves exact JSON, admission, string errors and state delivery without retries', async () => {
  const posts = [];
  const refusal = JSON.stringify({ code: 'invalidRequest', effect: 'none', recovery: 'none' });
  const page = relay(async item => {
    posts.push(item);
    if (item.command === 'app.locale.set') throw new Error(refusal);
    return '{}';
  });
  const raw = '{"schema":2,"locale":"en","locale":"zh-CN"}';
  const call = page.happyranchConnection.call('app.locale.set', raw);
  await Promise.resolve();
  assert.equal(posts.length, 0);
  page.__hrAppAdmit(page.__hrAppContext);
  await assert.rejects(call, error => error === refusal);
  assert.equal(posts.length, 1);
  assert.equal(posts[0].command, 'app.locale.set');
  assert.equal(posts[0].data, raw, 'native must receive duplicates unchanged for independent validation');
  await assert.rejects(page.happyranchConnection.call('app.locale.set', {}), error => JSON.parse(error).code === 'invalidRequest');
  assert.equal(posts.length, 1, 'invalid type cannot enter native and failures never retry');
  const states = [];
  const off = page.happyranchConnection.on('app.stateChanged', raw => states.push(raw));
  const state = '{"schema":2,"locale":{"current":"zh-CN","persisted":false}}';
  page.__hrAppState(state);
  off();
  page.__hrAppState('ignored');
  assert.deepEqual(states, [state]);
  assert.equal(posts.filter(item => item.command !== 'subscribe').length, 1, 'locale never sends a lifecycle/target command');
});
