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
  sharedRouterGuardNeeded,
} = await import('../src/lib/owners/ownerRegistry.ts');
const { navigationTransition, orgOfPath } = await import('../src/lib/owners/ownerTransitions.ts');

const ORG = { scope: 'settings', page: 'settings.organization', org: 'studio', computer: null, section: 'organization' };
const MAIN = { scope: 'main', page: 'agents.agent', subject: 'dev', org: 'studio', computer: null };
const CAPACITY = { scope: 'settings', page: 'settings.capacity', org: 'studio', computer: null, section: 'daemon-capacity', ownNavigationGuard: true };
const POLICY = { scope: 'main', page: 'agents.escalationPolicy', subject: 'mgr', org: 'studio', computer: null, ownNavigationGuard: true };

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

test('save error classification: 499 cancelled (ResultUnknownError) is unknown and holds the gate', () => {
  // Same fields as client.ts ResultUnknownError (499, 'cancelled'); the real
  // instance is checked in native-bridge-v2.test.mjs, which has the alias hooks.
  const resultUnknown = { status: 499, code: 'cancelled', detail: { code: 'cancelled' } };
  assert.equal(classifySaveError(resultUnknown), 'unknown');
  assert.equal(classifySaveError({ status: 499, code: null }), 'unknown');
  assert.equal(classifySaveError({ status: 400, code: 'cancelled' }), 'unknown');
  // A sent save whose result is unknown is never discardable: hold, not prompt.
  const r = createOwnerRegistry();
  r.register('main', ORG);
  r.setDraft('main', { dirty: true, revision: 1 });
  const ticket = r.beginSave('main');
  r.settleSave(ticket, classifySaveError(resultUnknown));
  const [owner] = r.getSnapshot();
  assert.equal(owner.status, 'unknown');
  assert.equal(selectOwnerBlocking(r.getSnapshot()).level, 'hold');
  assert.equal(r.discard('main'), false);
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

// ── PR #1775 review round 1 (TASK-18190) ──

test('R1-7: an unknown result refuses a new save; only reconciliation re-permits one', () => {
  const r = createOwnerRegistry();
  r.register('a', ORG);
  r.setDraft('a', { dirty: true, revision: 1 });
  r.settleSave(r.beginSave('a'), 'unknown');
  assert.equal(r.beginSave('a'), null, 'unknown is not retry authority');
  assert.equal(status(r, 'a'), 'unknown');
  r.reconcile('a', 'unknown');
  assert.equal(r.beginSave('a'), null, 'a still-unknown check keeps refusing');
  r.reconcile('a', 'not_saved');
  assert.equal(status(r, 'a'), 'failed');
  const retry = r.beginSave('a');
  assert.notEqual(retry, null, 'a confirmed failure permits an ordinary retry');
  assert.equal(status(r, 'a'), 'pending');
});

test('R1-2: a ticket bound to the revision the request was built from keeps a newer draft unsaved', () => {
  const r = createOwnerRegistry();
  r.register('a', ORG);
  r.setDraft('a', { dirty: true, revision: 1 }); // patch built from revision 1
  r.setDraft('a', { dirty: true, revision: 2 }); // edited while identities resolve
  const ticket = r.beginSave('a', 1);
  assert.equal(ticket.revision, 1);
  assert.deepEqual(r.settleSave(ticket, 'saved'), { current: false, known: true });
  assert.equal(status(r, 'a'), 'dirty');
});

test('R1-4: release forgets a resolved owner but keeps a pending/unknown one detached until resolved', () => {
  const r = createOwnerRegistry();
  r.register('clean', MAIN);
  r.release('clean');
  assert.equal(r.has('clean'), false);
  r.register('dirty', MAIN);
  r.setDraft('dirty', { dirty: true, revision: 1 });
  r.release('dirty');
  assert.equal(r.has('dirty'), false, 'unsent edits of an unmounted editor are gone');

  r.register('a', ORG);
  r.setDraft('a', { dirty: true, revision: 1 });
  const ticket = r.beginSave('a');
  r.release('a');
  assert.equal(r.has('a'), true);
  assert.equal(r.getSnapshot()[0].detached, true);
  assert.equal(selectOwnerBlocking(r.getSnapshot()).level, 'hold', 'a sent save keeps holding after teardown');
  r.settleSave(ticket, 'unknown');
  assert.equal(status(r, 'a'), 'unknown');
  assert.equal(r.discard('a'), false);
  r.reconcile('a', 'saved');
  assert.equal(r.has('a'), false, 'forgotten once reconciled');
  assert.equal(selectOwnerBlocking(r.getSnapshot()).level, 'none');
});

test('R1-6/R1-8: shared router guard composes self-guarded editors', () => {
  const r = createOwnerRegistry();
  r.register('cap', CAPACITY);
  assert.equal(sharedRouterGuardNeeded(r.getSnapshot()), false);
  r.setDraft('cap', { dirty: true, revision: 1 });
  assert.equal(sharedRouterGuardNeeded(r.getSnapshot()), false, 'sole self-guarded dirty editor keeps its own dialog');
  const ticket = r.beginSave('cap');
  assert.equal(sharedRouterGuardNeeded(r.getSnapshot()), true, 'a pending save never goes to a private Discard');
  r.settleSave(ticket, 'unknown');
  assert.equal(sharedRouterGuardNeeded(r.getSnapshot()), true, 'an unknown save never goes to a private Discard');
  r.reconcile('cap', 'not_saved');
  assert.equal(sharedRouterGuardNeeded(r.getSnapshot()), false);
  r.register('policy', POLICY);
  assert.equal(sharedRouterGuardNeeded(r.getSnapshot()), true, 'two private guards would shadow each other');
  r.unregister('cap');
  r.setDraft('policy', { dirty: true, revision: 1 });
  assert.equal(sharedRouterGuardNeeded(r.getSnapshot()), false, 'sole dirty policy page keeps its own dialog');
  r.register('s', ORG);
  r.setDraft('s', { dirty: true, revision: 1 });
  assert.equal(sharedRouterGuardNeeded(r.getSnapshot()), true, 'another unsaved owner mounts the shared blocker');
  const [policy] = r.getSnapshot();
  const leave = navigationTransition({ pathname: '/orgs/studio/agents/mgr/team-escalation-policy', search: '' }, { pathname: '/orgs/studio/dashboard', search: '' });
  assert.deepEqual(selectOwnerBlocking(r.getSnapshot(), leave.affects).owners.map((o) => o.id), [policy.id], 'the shared blocker includes the policy draft');
});

test('W04/W07 keyless acknowledgement is opt-in, observed in this episode and releases only self', () => {
  const r = createOwnerRegistry();
  for (const id of ['a', 'b']) {
    r.register(id, ORG);
    r.setDraft(id, { dirty: true, revision: 1 });
  }
  const a = r.beginSave('a');
  const b = r.beginSave('b');
  r.settleSave(a, 'unknown');
  r.settleSave(b, 'unknown');
  assert.equal(r.acknowledgeUnknown('a'), false, 'other owners do not opt in');
  r.setUnknownAcknowledgement('a', true);
  assert.equal(r.beginSave('a'), null, 'unknown cannot resend');
  assert.equal(r.acknowledgeUnknown('a'), false, 'no successful list observation');
  const failed = r.beginUnknownCheck('a');
  assert.ok(failed);
  assert.equal(r.acknowledgeUnknown('a'), false, 'failed/cancelled read did not complete');
  assert.equal(status(r, 'a'), 'unknown');
  assert.equal(r.getSnapshot().find((o) => o.id === 'a').savedRevision, null);
  const checked = r.beginUnknownCheck('a');
  assert.equal(r.completeUnknownCheck(checked), true);
  assert.equal(status(r, 'a'), 'unknown', 'list never attributes a save');
  assert.equal(r.acknowledgeUnknown('a'), true);
  assert.equal(r.has('a'), false, 'no saved/failed/dirty owner remains');
  assert.equal(status(r, 'b'), 'unknown');
  assert.equal(selectOwnerBlocking(r.getSnapshot()).level, 'hold');
  assert.deepEqual(r.settleSave(a, 'saved'), { current: false, known: false });
  assert.equal(r.completeUnknownCheck(checked), false);
  assert.equal(r.has('a'), false, 'late result cannot resurrect acknowledged owner');
});

test('W04 own late success settles before acknowledgement and invalidates old observations', () => {
  const r = createOwnerRegistry();
  r.register('a', ORG);
  r.setDraft('a', { dirty: true, revision: 1 });
  r.setUnknownAcknowledgement('a', true);
  const first = r.beginSave('a');
  r.settleSave(first, 'unknown');
  const check = r.beginUnknownCheck('a');
  assert.deepEqual(r.settleSave(first, 'saved'), { current: true, known: true });
  assert.equal(status(r, 'a'), 'confirmed');
  assert.equal(r.completeUnknownCheck(check), false, 'overtaken list cannot restore unknown');
  assert.equal(r.acknowledgeUnknown('a'), false);
  r.setDraft('a', { dirty: true, revision: 2 });
  const second = r.beginSave('a');
  r.settleSave(second, 'unknown');
  assert.equal(r.completeUnknownCheck(check), false, 'prior episode cannot authorize acknowledgement');
  assert.equal(r.acknowledgeUnknown('a'), false);
  const currentCheck = r.beginUnknownCheck('a');
  assert.equal(r.completeUnknownCheck(currentCheck), true);
  assert.equal(r.acknowledgeUnknown('a'), true);
});
