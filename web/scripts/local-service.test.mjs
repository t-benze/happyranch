// Focused Node 24 parser checks; no Vitest or Product E2E selection.
// node --experimental-transform-types --test web/scripts/local-service.test.mjs
import { registerHooks } from 'node:module';
import { existsSync, readFileSync } from 'node:fs';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { dirname, resolve } from 'node:path';
import { test } from 'node:test';
import assert from 'node:assert/strict';
const src = resolve(dirname(fileURLToPath(import.meta.url)), '../src');
registerHooks({ resolve(specifier, context, next) {
  let base;
  if (specifier.startsWith('@/')) base = resolve(src, specifier.slice(2));
  else if (specifier.startsWith('.') && context.parentURL?.endsWith('.ts')) base = resolve(dirname(fileURLToPath(context.parentURL)), specifier);
  if (base) for (const path of [base + '.ts', base + '/index.ts']) if (existsSync(path)) return next(pathToFileURL(path).href, context);
  return next(specifier, context);
} });
const local = await import('../src/features/settings/local/localService.ts');
const { parseAppState } = await import('../src/lib/native/appStatus.ts');
const schema = JSON.parse(readFileSync(new URL('../../protocol/native-connection-bridge.v2.json', import.meta.url)));
const mac = { supported: true, mode: 'appProcess', running: 'running', keepRunning: 'unavailable', busy: false };
const status = service => JSON.stringify({ schema: 2, service });

test('Mac appProcess is accepted with unavailable keep-running, including external observations', () => {
  for (const supported of [true, false]) {
    const value = { ...mac, supported };
    assert.deepEqual(local.parseLocalServiceResult(status(value)), value);
  }
  assert.ok(schema.$defs.localServiceStatus.properties.mode.enum.includes('appProcess'));
  assert.ok(schema.$defs.localServiceStatus.properties.keepRunning.enum.includes('unavailable'));
});

test('new closed values reject misspellings, impossible lifetime pairs and extra fields', () => {
  for (const value of [
    { ...mac, mode: 'app-process' }, { ...mac, keepRunning: 'notAvailable' },
    { ...mac, keepRunning: 'off' }, { ...mac, mode: 'service' },
    { ...mac, target: 'remote' }, { ...mac, busy: 'false' },
  ]) assert.equal(local.parseLocalServiceResult(status(value)), null, JSON.stringify(value));
});

test('Linux service and foreground snapshots retain their exact parsed payloads', () => {
  for (const keepRunning of ['checking', 'on', 'off', 'partial', 'unknown']) {
    const value = { supported: true, mode: 'service', running: 'running', keepRunning, busy: false };
    assert.deepEqual(local.parseLocalServiceResult(status(value)), value);
  }
  const foreground = { supported: false, mode: 'foreground', running: 'running', keepRunning: 'unknown', busy: false };
  assert.deepEqual(local.parseLocalServiceResult(status(foreground)), foreground);
});

test('Mac Start/Restart require readmission; remote appState accepts appProcess', () => {
  const readmission = { state: 'incompatible', incompatible: { side: 'app', host_contract: { current: 2, min_client: 2 }, client_contract: 1, min_daemon: 1 } };
  const remote = { schema: 2, generation: 'g1', target: { kind: 'remote', id: 'studio', name: 'Studio' }, state: 'ready', incompatible: null,
    local: { supported: true, mode: 'appProcess', running: 'running', prompt_base_url: null }, candidate: null, access_unsaved: false, locale: { current: 'en', persisted: true } };
  const remoteRaw = JSON.stringify(remote);
  assert.deepEqual(parseAppState(remoteRaw), remote);
  for (const command of ['local.start', 'local.restart']) {
    const value = { schema: 2, command, service: mac, readmission };
    assert.deepEqual(local.parseLocalActionResult(JSON.stringify(value), command), { service: mac, readmission });
    delete value.readmission;
    assert.equal(local.parseLocalActionResult(JSON.stringify(value), command), null);
  }
  assert.equal(local.parseLocalActionResult(JSON.stringify({ schema: 2, command: 'local.stop', service: mac, readmission }), 'local.stop'), null);
  assert.deepEqual(local.parseLocalActionResult(JSON.stringify({ schema: 2, command: 'local.stop', service: mac }), 'local.stop'), { service: mac, readmission: null });
});
