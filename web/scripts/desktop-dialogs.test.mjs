// Focused Node 24 checks for the THR-387 S6-W1 desktop dialog model; no Vitest
// or Product E2E selection. Run from the repository root:
//   node --experimental-transform-types --test web/scripts/desktop-dialogs.test.mjs
import { test } from 'node:test';
import assert from 'node:assert/strict';

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
  for (const page of ['preferences', 'daemon-capacity', 'executors', 'organization', 'coding']) {
    assert.deepEqual(settingsRequestFromPath(`/orgs/acme/settings/${page}`), { dialog: 'settings', section: page });
  }
  assert.deepEqual(settingsRequestFromPath('/orgs/acme/settings/organization/extra'), { dialog: 'settings', section: 'organization' });
});

test('bare, legacy and unknown org pages select Capacity, as the page route redirects', () => {
  for (const path of ['/orgs/acme/settings', '/orgs/acme/settings/', '/orgs/acme/settings/system', '/orgs/acme/settings/assistant', '/orgs/acme/settings/agents', '/orgs/acme/settings/nope']) {
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
  const settings = applyOpenRequest(CLOSED, { dialog: 'settings', section: 'coding' });
  assert.deepEqual(settings, { dialog: 'settings', section: 'coding' });
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
  const shown = { dialog: 'settings', section: 'coding' };
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
