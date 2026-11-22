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
const posts = [];
let receipt;
const window = {
  fetch() { throw Error('Unexpected network'); },
  webkit: { messageHandlers: { appTransport: { postMessage(item) {
    posts.push(item);
    return new Promise(resolve => { receipt = resolve; });
  } } } },
};
vm.runInNewContext(page, { window, top: window, location: { origin: 'happyranch-app://app' }, Map, Object });
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
assert.equal(posts.length, 1, 'pending owner summary must reach native');
assert.equal(posts[0].command, 'app.ownersChanged');
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
console.log('PASS: shipped owner push, delayed acknowledgement and refused receipt');
