// Focused Node 24 checks for the THR-387 S6-W2a config owner registry and
// gate transition rules; local evidence only (not wired into CI, no Vitest or
// Product E2E selection). Run from the repository root:
//   node --experimental-transform-types --test web/scripts/owner-registry.test.mjs
import { test } from 'node:test';
import assert from 'node:assert/strict';

const {
  createOwnerRegistry,
  selectOwnerBlocking,
  classifySaveError,
} = await import('../src/lib/owners/ownerRegistry.ts');
const { navigationTransition, orgOfPath } = await import('../src/lib/owners/ownerTransitions.ts');

const ORG = { scope: 'settings', page: 'settings.organization', org: 'studio', computer: null, section: 'organization' };
const MAIN = { scope: 'main', page: 'agents.agent', subject: 'dev', org: 'studio', computer: null };

function status(registry, id) {
  return registry.getSnapshot().find((owner) => owner.id === id)?.status;
}

test('draft, pending, matching success and confirmed states', () => {
  const r = createOwnerRegistry();
  r.register('a', ORG);
  assert.equal(status(r, 'a'), 'clean');
  r.setDraft('a', { dirty: true, revision: 1 });
  assert.equal(status(r, 'a'), 'dirty');
  const ticket = r.beginSave('a');
  assert.equal(ticket.revision, 1);
  assert.equal(status(r, 'a'), 'pending');
  assert.deepEqual(r.settleSave(ticket, 'saved'), { current: true, known: true });
  assert.equal(status(r, 'a'), 'confirmed');
});

test('an older save success never marks a newer draft saved', () => {
  const r = createOwnerRegistry();
  r.register('a', ORG);
  r.setDraft('a', { dirty: true, revision: 1 });
  const older = r.beginSave('a');
  r.setDraft('a', { dirty: true, revision: 2 }); // edited during the save
  const result = r.settleSave(older, 'saved');
  assert.equal(result.current, false);
  assert.equal(status(r, 'a'), 'dirty');
  const record = r.getSnapshot()[0];
  assert.equal(record.savedRevision, 1);
  assert.equal(record.draftRevision, 2);
});

test('an older failure does not mark a newer in-flight save failed', () => {
  const r = createOwnerRegistry();
  r.register('a', ORG);
  r.setDraft('a', { dirty: true, revision: 1 });
  const first = r.beginSave('a');
  r.setDraft('a', { dirty: true, revision: 2 });
  const second = r.beginSave('a');
  r.settleSave(first, 'failed');
  assert.equal(status(r, 'a'), 'pending');
  r.settleSave(second, 'saved');
  assert.equal(status(r, 'a'), 'confirmed');
});

test('confirmed failure keeps the draft unsaved; unknown holds until reconciled', () => {
  const r = createOwnerRegistry();
  r.register('a', ORG);
  r.setDraft('a', { dirty: true, revision: 1 });
  r.settleSave(r.beginSave('a'), 'failed');
  assert.equal(status(r, 'a'), 'failed');
  r.settleSave(r.beginSave('a'), 'unknown');
  assert.equal(status(r, 'a'), 'unknown');
  assert.equal(r.discard('a'), false, 'a sent save is never discardable');
  r.reconcile('a', 'unknown');
  assert.equal(status(r, 'a'), 'unknown');
  r.reconcile('a', 'not_saved');
  assert.equal(status(r, 'a'), 'failed');
  r.settleSave(r.beginSave('a'), 'unknown');
  r.reconcile('a', 'saved');
  assert.equal(status(r, 'a'), 'confirmed');
});

test('discard is refused while pending and clears only unsent edits', () => {
  const r = createOwnerRegistry();
  r.register('a', ORG);
  r.setDraft('a', { dirty: true, revision: 1 });
  const ticket = r.beginSave('a');
  assert.equal(r.discard('a'), false);
  r.settleSave(ticket, 'failed');
  assert.equal(r.discard('a'), true);
  assert.equal(status(r, 'a'), 'clean');
});

test('aggregate: hold beats prompt; filters to affected owners; lists Main and Settings together', () => {
  const r = createOwnerRegistry();
  r.register('s', ORG);
  r.register('m', MAIN);
  assert.equal(selectOwnerBlocking(r.getSnapshot()).level, 'none');
  r.setDraft('s', { dirty: true, revision: 1 });
  r.setDraft('m', { dirty: true, revision: 1 });
  const both = selectOwnerBlocking(r.getSnapshot());
  assert.equal(both.level, 'prompt');
  assert.deepEqual(both.owners.map((o) => o.id), ['s', 'm']);
  r.beginSave('m');
  assert.equal(selectOwnerBlocking(r.getSnapshot()).level, 'hold');
  const settingsOnly = selectOwnerBlocking(r.getSnapshot(), (o) => o.scope === 'settings');
  assert.equal(settingsOnly.level, 'prompt');
  assert.deepEqual(settingsOnly.owners.map((o) => o.id), ['s']);
});

test('unregister removes the owner; settle after unmount is known:false', () => {
  const r = createOwnerRegistry();
  r.register('a', ORG);
  r.setDraft('a', { dirty: true, revision: 1 });
  const ticket = r.beginSave('a');
  r.unregister('a');
  assert.equal(r.getSnapshot().length, 0);
  assert.equal(r.settleSave(ticket, 'saved').known, false);
});

test('save error classification: only a 4xx refusal is a confirmed failure', () => {
  assert.equal(classifySaveError({ status: 422, code: null }), 'failed');
  assert.equal(classifySaveError({ status: 403, code: null }), 'failed');
  assert.equal(classifySaveError({ status: 409, code: 'stale_revision' }), 'failed');
  assert.equal(classifySaveError({ status: 409, code: 'target_changed' }), 'unknown');
  assert.equal(classifySaveError({ status: 408, code: null }), 'unknown');
  assert.equal(classifySaveError({ status: 502, code: null }), 'unknown');
  assert.equal(classifySaveError({ status: 503, code: 'target_unreachable' }), 'unknown');
  assert.equal(classifySaveError(new TypeError('Failed to fetch')), 'unknown');
  assert.equal(classifySaveError(null), 'unknown');
});

test('navigation: page change affects Main owners; org change affects Main and Settings', () => {
  const r = createOwnerRegistry();
  r.register('s', ORG);
  r.register('m', MAIN);
  const [s, m] = r.getSnapshot();
  const page = navigationTransition({ pathname: '/orgs/studio/agents/dev', search: '' }, { pathname: '/orgs/studio/dashboard', search: '' });
  assert.equal(page.effect, 'leave');
  assert.equal(page.affects(m), true);
  assert.equal(page.affects(s), false);
  const org = navigationTransition({ pathname: '/orgs/studio/agents/dev', search: '' }, { pathname: '/orgs/sandbox/dashboard', search: '' });
  assert.equal(org.effect, 'changeOrg');
  assert.equal(org.affects(m), true);
  assert.equal(org.affects(s), true);
  const same = navigationTransition({ pathname: '/orgs/studio/agents', search: '?x=1' }, { pathname: '/orgs/studio/agents', search: '?x=1' });
  assert.equal(same.affects(m), false);
  assert.equal(orgOfPath('/orgs/studio'), 'studio');
  assert.equal(orgOfPath('/settings/preferences'), null);
});
