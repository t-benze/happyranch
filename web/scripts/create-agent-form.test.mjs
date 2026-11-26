// Focused Node 24 check for the THR-402 Create team / Create agent form rules
// (`web/src/features/agents/createAgentForm.ts`): the new-input grammars the
// backend shares, the editable team suggestion, the one-team-field request
// body, the outcome classes (refused / setup incomplete / unknown) and the
// Check result matcher. Local evidence only (not wired into CI). Run from the
// repository root:
//   node --experimental-transform-types --test web/scripts/create-agent-form.test.mjs
import { test } from 'node:test';
import assert from 'node:assert/strict';

const form = await import('../src/features/agents/createAgentForm.ts');

test('agent name: 1-64 ASCII, letter first, then letters/digits/_/-', () => {
  const cases = [
    ['', 'required'],
    ['a', null],
    ['A'.repeat(64), null],
    ['A'.repeat(65), 'invalid'],
    ['Consultant-Claude_2', null],
    ['1abc', 'invalid'],
    ['_abc', 'invalid'],
    ['ab c', 'invalid'],
    [' abc', 'invalid'],
    ['abç', 'invalid'],
    ['ab.c', 'invalid'],
  ];
  for (const [name, problem] of cases) assert.equal(form.agentNameProblem(name), problem, JSON.stringify(name));
});

test('new team name: shared grammar, default reserved in any case, duplicates refused', () => {
  const existing = ['engineering', 'Legacy-Team'];
  const cases = [
    ['', 'required'],
    ['a', null],
    ['a'.repeat(64), null],
    ['a'.repeat(65), 'invalid'],
    ['dev_ops2', null],
    ['Dev', 'invalid'],
    ['dev-ops', 'invalid'],
    ['dev ops', 'invalid'],
    ['2dev', 'invalid'],
    ['_dev', 'invalid'],
    ['default', 'reserved'],
    ['Default', 'reserved'],
    ['DEFAULT', 'reserved'],
    ['engineering', 'exists'],
  ];
  for (const [team, problem] of cases) assert.equal(form.newTeamProblem(team, existing), problem, JSON.stringify(team));
  // Unknown roster: no duplicate claim (the server stays authoritative).
  assert.equal(form.newTeamProblem('engineering', null), null);
});

test('team suggestion: lowercase, hyphen to underscore, drop the last _suffix', () => {
  assert.equal(form.suggestTeamName(''), '');
  assert.equal(form.suggestTeamName('Alpha'), 'alpha');
  assert.equal(form.suggestTeamName('Dev-Lead'), 'dev');
  assert.equal(form.suggestTeamName('growth_ops_manager'), 'growth_ops');
  assert.equal(form.suggestTeamName('x_'), 'x');
});

test('request body carries exactly one team field for the current role', () => {
  const draft = {
    name: 'Ada', team: 'engineering', newTeam: 'research', executor: 'claude',
    description: 'd', systemPrompt: 'p',
  };
  const worker = form.buildCreateBody({ ...draft, role: 'worker' });
  assert.deepEqual(worker, { display_name: 'Ada', role: 'worker', team: 'engineering', executor: 'claude', description: 'd', system_prompt: 'p' });
  assert.equal('new_team' in worker, false, 'a worker never submits the hidden new team name');
  const manager = form.buildCreateBody({ ...draft, role: 'manager' });
  assert.deepEqual(manager, { display_name: 'Ada', role: 'manager', new_team: 'research', executor: 'claude', description: 'd', system_prompt: 'p' });
  assert.equal('team' in manager, false);
  assert.equal('name' in manager, false, 'never manufactures a permanent ID');
});

test('server codes map to their field; unrelated codes to none', () => {
  const expect = {
    invalid_team_name: 'newTeam', team_reserved: 'newTeam', team_exists: 'newTeam',
    unknown_team: 'team', agent_exists: 'name', identity_name_unavailable: 'name',
    identity_name_reserved: 'name', invalid_identity_label: 'name', agent_id_exhausted: 'name',
    invalid_executor: 'executor', missing_required_field: null, role_team_mismatch: null,
    something_new: null, constructor: null,
  };
  for (const [code, field] of Object.entries(expect)) assert.equal(form.fieldForCode(code), field, code);
  assert.equal(form.fieldForCode(null), null);
});

const apiError = (status, code, detail = code ? { code } : null) => ({ name: 'ApiError', status, code, detail });

test('outcome classes: refused before effect, setup incomplete, unknown', () => {
  assert.deepEqual(form.classifyCreateError(apiError(422, 'invalid_team_name')), { kind: 'refused', status: 422, code: 'invalid_team_name' });
  assert.deepEqual(form.classifyCreateError(apiError(409, 'team_reserved')), { kind: 'refused', status: 409, code: 'team_reserved' });
  assert.deepEqual(form.classifyCreateError(apiError(404, 'unknown_team')), { kind: 'refused', status: 404, code: 'unknown_team' });
  assert.deepEqual(form.classifyCreateError(apiError(401, null)), { kind: 'refused', status: 401, code: null });
  assert.deepEqual(form.classifyCreateError(apiError(403, 'remote_read_only')), { kind: 'refused', status: 403, code: 'remote_read_only' });
  assert.deepEqual(
    form.classifyCreateError(apiError(500, 'agent_setup_incomplete', { code: 'agent_setup_incomplete', canonical_id: 'ada', team: 'research', role: 'manager' })),
    { kind: 'setupIncomplete', canonicalId: 'ada', team: 'research', role: 'manager' },
  );
  for (const err of [
    apiError(500, null), apiError(503, 'upstream'), apiError(408, null), apiError(499, 'cancelled'),
    apiError(409, 'target_changed'), new TypeError('Failed to fetch'), null, 'boom',
  ]) {
    assert.deepEqual(form.classifyCreateError(err), { kind: 'unknown' }, JSON.stringify(err));
  }
});

test('Check result: full match resolves, a name alone never does, absence means not created', () => {
  const sent = form.buildCreateBody({
    name: 'Ada', role: 'manager', team: '', newTeam: 'research', executor: 'claude',
    description: 'Leads research', systemPrompt: 'You lead research.',
  });
  const identities = [
    { canonical_id: 'ada', kind: 'agent', addressable_name: 'Ada' },
    { canonical_id: 'founder', kind: 'founder', addressable_name: 'Founder' },
  ];
  const row = {
    name: 'ada', addressable_name: 'Ada', team: 'research', role: 'manager', executor: 'claude',
    description: 'Leads research', system_prompt: 'You lead research.',
  };
  assert.deepEqual(form.findCreatedAgent(sent, [row], identities), { kind: 'matched', canonicalId: 'ada' });
  // Case-insensitive name, metadata missing on an older roster (identity fallback).
  assert.deepEqual(
    form.findCreatedAgent({ ...sent, display_name: 'ADA' }, [{ ...row, addressable_name: undefined }], identities),
    { kind: 'matched', canonicalId: 'ada' },
  );
  assert.deepEqual(form.findCreatedAgent(sent, [{ ...row, team: 'other' }], identities), { kind: 'nameOnly', canonicalId: 'ada' });
  assert.deepEqual(form.findCreatedAgent(sent, [{ ...row, system_prompt: 'else' }], identities), { kind: 'nameOnly', canonicalId: 'ada' });
  // Only a (e.g. terminated) identity carries the name: still not proof.
  assert.deepEqual(form.findCreatedAgent(sent, [], identities), { kind: 'nameOnly', canonicalId: 'ada' });
  assert.deepEqual(form.findCreatedAgent(sent, [], identities.slice(1)), { kind: 'absent' });
});
