// Render the real page with test-side native/i18n inputs; no production test seam.
// After the repository's npm ci:
// node --experimental-transform-types --test web/scripts/local-service-page.test.mjs
import { registerHooks, createRequire } from 'node:module';
import { existsSync, readFileSync } from 'node:fs';
import { dirname, resolve } from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { test } from 'node:test';
import assert from 'node:assert/strict';
import ts from '../node_modules/typescript/lib/typescript.js';
import React from '../node_modules/react/index.js';
import { renderToStaticMarkup } from '../node_modules/react-dom/server.node.js';
const src = resolve(dirname(fileURLToPath(import.meta.url)), '../src');
registerHooks({
  resolve(specifier, context, next) {
    if (specifier === '@/lib/native/hooks') return { url: 'local-test:status', shortCircuit: true };
    if (specifier === '@/hooks/i18n') return { url: 'local-test:i18n', shortCircuit: true };
    let base;
    if (specifier.startsWith('@/')) base = resolve(src, specifier.slice(2));
    else if (specifier.startsWith('.') && /\.tsx?$/.test(context.parentURL ?? '')) base = resolve(dirname(fileURLToPath(context.parentURL)), specifier);
    if (base) for (const path of [base + '.ts', base + '.tsx', base + '/index.ts']) if (existsSync(path)) return next(pathToFileURL(path).href, context);
    return next(specifier, context);
  },
  load(url, context, next) {
    if (url === 'local-test:status') return { format: 'module', source: 'export function useNativeAppStatus() { return globalThis.localPageApp; }', shortCircuit: true };
    if (url === 'local-test:i18n') return { format: 'module', source: 'export function useTranslation() { return { t: globalThis.localPageTranslation }; }', shortCircuit: true };
    if (url.endsWith('.tsx')) return { format: 'module', source: ts.transpileModule(readFileSync(fileURLToPath(url), 'utf8'), { compilerOptions: { jsx: ts.JsxEmit.ReactJSX, module: ts.ModuleKind.ESNext } }).outputText, shortCircuit: true };
    return next(url, context);
  },
});
const { en } = await import('../src/lib/i18n/locales/en.ts');
const { zhCN } = await import('../src/lib/i18n/locales/zh-CN.ts');
const { LocalServicePage } = await import('../src/features/settings/local/LocalServicePage.tsx');
function render(mode, catalog) {
  globalThis.localPageApp = { schema: 2, generation: 'g1', target: { kind: 'local', id: 'local', name: 'This computer' }, state: 'ready', incompatible: null,
    local: { supported: true, mode, running: 'running', prompt_base_url: null }, candidate: null, access_unsaved: false, locale: { current: catalog === en ? 'en' : 'zh-CN', persisted: true } };
  globalThis.localPageTranslation = (key, params = {}) => {
    const value = catalog[key];
    assert.equal(typeof value, 'string', key);
    return value.replace(/\{([^}]+)\}/g, (_, name) => params[name] ?? name);
  };
  const html = renderToStaticMarkup(React.createElement(LocalServicePage));
  return html.match(/<section data-testid="local-keep-row"[\s\S]*?<\/section>/)?.[0];
}
for (const [locale, catalog, copy] of [['en', en, 'Not available on Mac'], ['zh-CN', zhCN, 'Mac 上不可用']]) {
  test(`Mac ${locale} renders a plain unavailable state, including initial status loading`, () => {
    const row = render('appProcess', catalog);
    assert.ok(row?.includes(copy), row);
    assert.ok(!row.includes('role="switch"') && !row.includes('<button'), row);
  });
  test(`Linux ${locale} retains the keep-running switch while checking`, () => {
    const row = render('service', catalog);
    assert.ok(row?.includes('role="switch"'), row);
    assert.ok(row.includes('disabled=""'), row);
    assert.ok(!row.includes(copy), row);
  });
}

// Same existing Ajv 6 compatibility venue as native-locale.test.mjs.
const require = createRequire(import.meta.url);
const Ajv = require('../node_modules/ajv');
const schema = JSON.parse(readFileSync(new URL('../../protocol/native-connection-bridge.v2.json', import.meta.url), 'utf8').replaceAll('#/$defs/', '#/definitions/'));
schema.definitions = schema.$defs;
delete schema.$defs;
delete schema.$schema;
const validator = new Ajv({ allErrors: true });
validator.addSchema(schema, 'bridge');
const valid = validator.getSchema('bridge#/definitions/localStatusResult');
test('protocol schema admits Mac observations and preserves the Linux closed values', () => {
  const mac = { supported: true, mode: 'appProcess', running: 'running', keepRunning: 'unavailable', busy: false };
  for (const supported of [true, false]) assert.ok(valid({ schema: 2, service: { ...mac, supported } }), JSON.stringify(valid.errors));
  for (const value of [{ ...mac, keepRunning: 'off' }, { ...mac, mode: 'service' }, { ...mac, extra: true }]) assert.equal(valid({ schema: 2, service: value }), false);
  for (const keepRunning of ['on', 'off', 'partial', 'checking', 'unknown']) {
    assert.ok(valid({ schema: 2, service: { ...mac, mode: 'service', keepRunning } }), JSON.stringify(valid.errors));
  }
  assert.ok(valid({ schema: 2, service: { supported: false, mode: 'foreground', running: 'running', keepRunning: 'unknown', busy: false } }));
});

const { NativeRecoveryPanel } = await import('../src/shared/connection/NativeRecoveryPanel.tsx');
for (const [locale, catalog, reopen, recheck, relaunch] of [
  ['en', en, 'After updating, quit HappyRanch and open it again.', 'Recheck', 'Relaunch'],
  ['zh-CN', zhCN, '更新后，请退出 HappyRanch 并重新打开。', '重新检查', '重新启动'],
]) {
  test(`unsupported Mac recovery ${locale} explains quit/reopen and keeps Recheck; Linux retains Relaunch`, () => {
    globalThis.localPageTranslation = key => { assert.equal(typeof catalog[key], 'string', key); return catalog[key]; };
    for (const supported of [undefined, false, true]) {
      globalThis.localPageApp = { state: 'incompatible', incompatible: { side: 'app' }, app_relaunch: supported };
      const html = renderToStaticMarkup(React.createElement(NativeRecoveryPanel, {
        state: { code: 'incompatible', side: 'app' }, onRetry() {},
      }));
      assert.equal(html.includes(reopen), supported !== true, html);
      assert.ok(html.includes(recheck), html);
      assert.equal(html.includes(`>${relaunch}</button>`), supported === true, html);
    }
    const host = renderToStaticMarkup(React.createElement(NativeRecoveryPanel, {
      state: { code: 'incompatible', side: 'host' }, onRetry() {},
    }));
    assert.ok(!host.includes(reopen), host);
  });
}
