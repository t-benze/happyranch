// Focused Node 24 checks for the THR-387 S6-W1 desktop dialog model; no Vitest
// or Product E2E selection. Run from the repository root:
//   node --experimental-transform-types --test web/scripts/desktop-dialogs.test.mjs
import { test } from 'node:test';
import assert from 'node:assert/strict';

const model = await import('../src/features/desktop-dialogs/dialogModel.ts');
const { applyOpenRequest, settingsRequestFromPath, isSettingsPath, CLOSED } = model;

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
