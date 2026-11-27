// Focused Node 24 checks for the THR-387 S6-W1 desktop dialog model; no Vitest
// or Product E2E selection. Run from the repository root:
//   node --experimental-transform-types --test web/scripts/desktop-dialogs.test.mjs
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { runInNewContext } from 'node:vm';

const model = await import('../src/features/desktop-dialogs/dialogModel.ts');
const {
  applyOpenRequest,
  settingsRequestFromPath,
  isSettingsPath,
  parseDialogRequested,
  nativeDialogRequest,
  dialogLinkTarget,
  CLOSED,
} = model;

test('Main Settings routes select exactly the requested page', () => {
  for (const page of ['preferences', 'daemon-capacity', 'executors', 'organization']) {
    assert.deepEqual(settingsRequestFromPath(`/orgs/acme/settings/${page}`), { dialog: 'settings', section: page });
  }
  assert.deepEqual(settingsRequestFromPath('/orgs/acme/settings/organization/extra'), { dialog: 'settings', section: 'organization' });
});

test('bare, legacy (incl. removed coding) and unknown org pages select Capacity', () => {
  for (const path of ['/orgs/acme/settings', '/orgs/acme/settings/', '/orgs/acme/settings/system', '/orgs/acme/settings/assistant', '/orgs/acme/settings/agents', '/orgs/acme/settings/coding', '/orgs/acme/settings/nope']) {
    assert.deepEqual(settingsRequestFromPath(path), { dialog: 'settings', section: 'daemon-capacity' }, path);
  }
});

test('org-less recovery Settings defaults to Language and keeps named pages', () => {
  assert.deepEqual(settingsRequestFromPath('/settings'), { dialog: 'settings', section: 'preferences' });
  assert.deepEqual(settingsRequestFromPath('/settings/whatever'), { dialog: 'settings', section: 'preferences' });
  assert.deepEqual(settingsRequestFromPath('/settings/executors'), { dialog: 'settings', section: 'executors' });
});

test('non-Settings paths are not Settings requests', () => {
  for (const path of ['/', '/orgs/acme/dashboard', '/orgs/acme/settingsx', '/settingsx', '/orgs/settings', '/onboarding']) {
    assert.equal(settingsRequestFromPath(path), null, path);
    assert.equal(isSettingsPath(path), false, path);
  }
});

test('one dialog at a time: opening the other replaces it', () => {
  const settings = applyOpenRequest(CLOSED, { dialog: 'settings', section: 'organization' });
  assert.deepEqual(settings, { dialog: 'settings', section: 'organization' });
  const connection = applyOpenRequest(settings, { dialog: 'connection' });
  assert.deepEqual(connection, { dialog: 'connection' });
  assert.deepEqual(applyOpenRequest(connection, { dialog: 'settings' }), { dialog: 'settings', section: 'daemon-capacity' });
});

test('repeated Settings entry without a page keeps the page already shown', () => {
  const current = { dialog: 'settings', section: 'executors' };
  assert.equal(applyOpenRequest(current, { dialog: 'settings' }), current);
  assert.deepEqual(applyOpenRequest(current, { dialog: 'settings', section: 'organization' }), { dialog: 'settings', section: 'organization' });
});

// Bridge spec "v2 dialog request event" examples, plus non-string/non-object data.
test('app.dialogRequested accepts exactly the two v2 payloads', () => {
  assert.equal(parseDialogRequested('{"schema":2,"dialog":"settings"}'), 'settings');
  assert.equal(parseDialogRequested('{"dialog":"connection","schema":2}'), 'connection');
});

test('invalid app.dialogRequested payloads are ignored', () => {
  for (const raw of [
    '{"schema":2,"dialog":"preferences"}',
    '{"schema":2,"dialog":"settings","target":"local"}',
    '{"schema":2,"dialog":"connection","generation":"g12"}',
    '{"schema":1,"dialog":"settings"}',
    '{"schema":"2","dialog":"settings"}',
    '{"schema":2}',
    '{"dialog":"settings"}',
    '{"schema":2,"dialog":"settings"',
    '["settings"]',
    'null',
    '"settings"',
    '',
    { schema: 2, dialog: 'settings' },
    undefined,
  ]) {
    assert.equal(parseDialogRequested(raw), null, JSON.stringify(raw));
  }
});

test('native Settings entry keeps the shown page, else defaults by Main context', () => {
  const inOrg = nativeDialogRequest('settings', '/orgs/acme/dashboard');
  const orgLess = nativeDialogRequest('settings', '/');
  assert.deepEqual(applyOpenRequest(CLOSED, inOrg), { dialog: 'settings', section: 'daemon-capacity' });
  assert.deepEqual(applyOpenRequest(CLOSED, orgLess), { dialog: 'settings', section: 'preferences' });
  const shown = { dialog: 'settings', section: 'organization' };
  assert.equal(applyOpenRequest(shown, orgLess), shown);
  assert.deepEqual(applyOpenRequest(shown, nativeDialogRequest('connection', '/')), { dialog: 'connection' });
});

const here = { protocol: 'happyranch-app:', host: 'app', pathname: '/orgs/acme/dashboard', search: '' };
const primary = { button: 0, modified: false };
const link = (href, extra = {}) => ({ href, target: '', hasDownload: false, ...extra });

test('in-pane same-document links become a Main destination', () => {
  assert.equal(dialogLinkTarget(link('happyranch-app://app/orgs/acme/workflows/setup'), primary, here), '/orgs/acme/workflows/setup');
  assert.equal(dialogLinkTarget(link('happyranch-app://app/orgs/acme/agents'), primary, here), '/orgs/acme/agents');
  assert.equal(dialogLinkTarget(link('happyranch-app://app/orgs/acme/work-hours?view=wakes#x'), primary, here), '/orgs/acme/work-hours?view=wakes#x');
  assert.equal(dialogLinkTarget(link('http://127.0.0.1:8000/orgs/acme/agents', { target: '_self' }), primary, { ...here, protocol: 'http:', host: '127.0.0.1:8000' }), '/orgs/acme/agents');
});

test('links the dialog must leave alone stay native', () => {
  assert.equal(dialogLinkTarget(link('https://example.com/docs'), primary, here), null);
  assert.equal(dialogLinkTarget(link('happyranch-app://other/orgs/acme/agents'), primary, here), null);
  assert.equal(dialogLinkTarget(link('happyranch-app://app/orgs/acme/agents', { target: '_blank' }), primary, here), null);
  assert.equal(dialogLinkTarget(link('happyranch-app://app/orgs/acme/agents', { hasDownload: true }), primary, here), null);
  assert.equal(dialogLinkTarget(link('happyranch-app://app/orgs/acme/agents'), { button: 1, modified: false }, here), null);
  assert.equal(dialogLinkTarget(link('happyranch-app://app/orgs/acme/agents'), { button: 0, modified: true }, here), null);
  assert.equal(dialogLinkTarget(link('happyranch-app://app/orgs/acme/dashboard#top'), primary, here), null);
  assert.equal(dialogLinkTarget(link('not a url'), primary, here), null);
});

// Execute the qualifier's JavaScript, with geometry supplied independently of
// its pane selection and page calculations. This does not simulate WebKit.
const qualification = readFileSync(new URL('../../app/mac/Sources/HappyRanchApp/AppSchemeQualification.swift', import.meta.url), 'utf8');
const scrollPlan = qualification.match(/static let dialogScrollPlan = #"""([\s\S]*?)"""#/)?.[1]
  ?? qualification.match(/dialogStep\("scroll-plan"[\s\S]*?callAsyncJavaScript\(#"""([\s\S]*?)"""#/)?.[1];
assert.ok(scrollPlan, 'execute the actual embedded scroll planner');
function capturePane({ top = 80, bottom = 520, height = 440, content = 1800, overflow = 'auto', parent = null } = {}) {
  return { clientHeight: height, scrollHeight: content, scrollTop: 20, parentElement: parent,
    style: { overflowY: overflow, visibility: 'visible', display: 'block' },
    getBoundingClientRect: () => ({ top, bottom, left: 0, right: 760, width: 760 }) };
}
function capturePlan(dialog, background = []) {
  const page = { innerHeight: 560, innerWidth: 760, window: {},
    getComputedStyle: e => e.style,
    document: { scrollingElement: background[0] ?? null,
      querySelector: () => dialog, querySelectorAll: () => [...background, ...(dialog ? [dialog, ...dialog.children] : [])] } };
  const result = runInNewContext(`(function () { ${scrollPlan} })()`, page);
  return { result: JSON.parse(JSON.stringify(result)), scrollers: Array.from(page.window.__hrCaptureScrollers) };
}
test('Mac 200% capture plans active Settings panes without spending pages on background Main', () => {
  const dialog = capturePane({ height: 560, content: 560, top: 0, bottom: 560, overflow: 'hidden' });
  const settings = capturePane({ parent: dialog });
  dialog.children = [settings]; dialog.querySelectorAll = () => dialog.children;
  const background = capturePane({ top: 559, bottom: 2000, height: 1441, content: 10000 });
  const { result, scrollers } = capturePlan(dialog, [background]);
  assert.deepEqual(scrollers, [settings]);
  assert.deepEqual(result, [{ index: 0, step: 330, maximum: 1360, pages: 5 }]);
  assert.equal(settings.scrollTop, 0);
  assert.equal(background.scrollTop, 20, 'a modal capture does not move background Main');
  const main = capturePane();
  assert.deepEqual(capturePlan(null, [main]).result, [{ index: 0, step: 330, maximum: 1360, pages: 5 }], 'Main still captures its own overflow');
});
test('Mac capture excludes panes clipped by ancestors from the finite page budget', () => {
  const dialog = capturePane({ height: 560, content: 560, top: 0, bottom: 560, overflow: 'hidden' });
  const hidden = capturePane({ top: 600, bottom: 900, overflow: 'hidden', parent: dialog });
  const clipped = capturePane({ parent: hidden });
  const settings = capturePane({ parent: dialog });
  dialog.children = [hidden, clipped, settings]; dialog.querySelectorAll = () => dialog.children;
  const { result, scrollers } = capturePlan(dialog);
  assert.deepEqual(scrollers, [settings]);
  assert.equal(result.reduce((n, p) => n + p.pages, 0), 5);
});
