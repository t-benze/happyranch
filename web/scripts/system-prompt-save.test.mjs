// Focused Node 24 check for the THR-387 S6-W2a system prompt settlement rule:
// an acknowledged save is settled `saved` only after a MATCHING verification
// read; a failed or disagreeing read keeps the owner held and never publishes
// a clean snapshot that a queued org change could consume. Runs the production
// save flow (`saveAndVerifySystemPrompt`) against the real owner registry and
// the gate's aggregate. Local evidence only (not wired into CI). Run from the
// repository root:
//   node --experimental-transform-types --test web/scripts/system-prompt-save.test.mjs
import { test } from 'node:test';
import assert from 'node:assert/strict';

const { createOwnerRegistry, selectOwnerBlocking, classifySaveError } = await import(
  '../src/lib/owners/ownerRegistry.ts'
);
const { saveAndVerifySystemPrompt } = await import('../src/features/agents/systemPromptSave.ts');

const PROMPT = { scope: 'main', page: 'agents.systemPrompt', subject: 'dev', org: 'studio', computer: null };
const REV_A = 'a'.repeat(64);
const REV_B = 'b'.repeat(64);
const REV_C = 'c'.repeat(64);
const validRevision = (value) => typeof value === 'string' && /^[0-9a-f]{64}$/.test(value);
// A queued "Back to Sandbox": the gate proceeds as soon as the owners it
// affects are no longer blocking (OwnerGatePrompt's level === 'none' effect).
const leavingStudio = (owner) => owner.org === 'studio';

/** Run one Save through the production flow and record every published gate level. */
async function runSave({ save, read }) {
  const registry = createOwnerRegistry();
  registry.register('prompt', PROMPT);
  registry.setDraft('prompt', { dirty: true, revision: 1 });
  const levels = [];
  registry.subscribe(() => levels.push(selectOwnerBlocking(registry.getSnapshot(), leavingStudio).level));
  const ticket = registry.beginSave('prompt');
  const done = await saveAndVerifySystemPrompt({
    agentName: 'dev',
    save,
    read,
    classifySaveError,
    validRevision,
  });
  registry.settleSave(ticket, done.outcome);
  const status = registry.getSnapshot().find((owner) => owner.id === 'prompt')?.status;
  return { registry, done, levels, status };
}

const receipt = { agent: 'dev', system_prompt: 'new prompt', revision: REV_B };

for (const [name, read] of [
  ['verification read disagrees (another prompt and revision)', async () => ({ system_prompt: 'other client', revision: REV_C })],
  ['verification read disagrees (same text, other revision)', async () => ({ system_prompt: 'new prompt', revision: REV_C })],
  ['verification read is empty', async () => null],
  ['verification read fails with 503', async () => { throw Object.assign(new Error('unavailable'), { status: 503 }); }],
]) {
  test(`acknowledged save, ${name}: held, never clean, not discardable`, async () => {
    const { registry, done, levels, status } = await runSave({ save: async () => receipt, read });
    assert.equal(done.outcome, 'unknown');
    assert.equal(status, 'unknown');
    // Every published state from Save to settlement blocks the queued org change.
    assert.ok(levels.length > 0);
    assert.deepEqual([...new Set(levels)], ['hold']);
    assert.equal(registry.discard('prompt'), false);
    assert.equal(registry.beginSave('prompt'), null);
  });
}

test('acknowledged save with a matching verification read settles and the queued change proceeds', async () => {
  const { done, levels, status } = await runSave({
    save: async () => receipt,
    read: async () => ({ system_prompt: 'new prompt', revision: REV_B }),
  });
  assert.equal(done.outcome, 'saved');
  assert.equal(done.result.kind, 'verified');
  assert.equal(status, 'confirmed');
  assert.equal(levels.at(-1), 'none');
});

test('a refused PUT is a confirmed failure; a lost PUT is unknown; neither reads', async () => {
  let reads = 0;
  const read = async () => { reads += 1; return { system_prompt: 'x', revision: REV_A }; };
  const refused = await runSave({ save: async () => { throw Object.assign(new Error('bad'), { status: 422 }); }, read });
  assert.equal(refused.done.outcome, 'failed');
  assert.equal(refused.status, 'failed');
  const lost = await runSave({ save: async () => { throw new TypeError('network'); }, read });
  assert.equal(lost.done.outcome, 'unknown');
  assert.equal(lost.status, 'unknown');
  assert.equal(reads, 0);
});
