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
