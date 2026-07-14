import { describe, expect, test } from 'vitest';
import {
  agentProvenance,
  agentProvenanceList,
  assignmentRollup,
  editRoutePath,
  isEditableSkill,
  isReadOnlySkill,
  needsAttention,
  readOnlyReason,
  skillSource,
  validationIssues,
} from './skills-detail';

describe('skills-detail — source gating', () => {
  test('user_authored is custom + editable', () => {
    const facts = { type: 'user_authored', system_contract: false };
    expect(skillSource(facts)).toBe('custom');
    expect(isEditableSkill(facts)).toBe(true);
    expect(isReadOnlySkill(facts)).toBe(false);
    expect(readOnlyReason(facts)).toBeNull();
  });

  test('managed bundled is read-only (no edit), reason omits "unassigned"', () => {
    const facts = { type: 'managed', system_contract: false };
    expect(skillSource(facts)).toBe('bundled');
    expect(isEditableSkill(facts)).toBe(false);
    expect(isReadOnlySkill(facts)).toBe(true);
    const reason = readOnlyReason(facts);
    expect(reason).toMatch(/managed by the platform/i);
    expect(reason).not.toMatch(/unassign/i);
  });

  test('system_contract is read-only + applied by context, cannot be unassigned', () => {
    const facts = { type: 'system_contract', system_contract: true };
    expect(skillSource(facts)).toBe('bundled');
    expect(isEditableSkill(facts)).toBe(false);
    expect(readOnlyReason(facts)).toMatch(/cannot be edited or unassigned/i);
  });
});

describe('skills-detail — validation / needs-attention', () => {
  test('failed_validation needs attention and surfaces issue lines', () => {
    const facts = {
      validation_state: 'failed_validation',
      validation: { ok: false, errors: ['SKILL.md is missing a version field', ''] },
    };
    expect(needsAttention(facts)).toBe(true);
    expect(validationIssues(facts)).toEqual([
      'SKILL.md is missing a version field',
    ]);
  });

  test('validated skill does not need attention and has no issues', () => {
    const facts = { validation_state: 'validated', validation: { ok: true, errors: [] } };
    expect(needsAttention(facts)).toBe(false);
    expect(validationIssues(facts)).toEqual([]);
  });

  test('validation.ok=false wins even if validation_state lags', () => {
    expect(needsAttention({ validation_state: 'in_catalog', validation: { ok: false } })).toBe(true);
  });
});

describe('skills-detail — per-agent provenance (guidance-visibility language)', () => {
  test('effective agent: positive, no next-session indicator, no "active"', () => {
    const p = agentProvenance({
      agent: 'dev_agent',
      assigned: true,
      effective: true,
      state: 'effective',
    });
    expect(p.status).toBe('effective');
    expect(p.statusLabel).toBe('Effective');
    expect(p.tone).toBe('positive');
    expect(p.takesEffectNextSession).toBe(false);
    expect(p.reason).not.toMatch(/\bactive\b/i);
    expect(p.reason).toMatch(/materialized/i);
  });

  test('assigned-not-yet-effective: attention + takes-effect-next-session', () => {
    const p = agentProvenance({
      agent: 'qa_agent',
      assigned: true,
      effective: false,
      state: 'assigned_not_yet_effective',
    });
    expect(p.status).toBe('not_yet_effective');
    expect(p.statusLabel).toBe('Takes effect next session');
    expect(p.tone).toBe('attention');
    expect(p.takesEffectNextSession).toBe(true);
    expect(p.reason).toMatch(/next session/i);
    expect(p.reason).not.toMatch(/\bactive\b/i);
  });

  test('not-assigned: neutral, explains it is not shown as guidance', () => {
    const p = agentProvenance({ agent: 'ops_agent', assigned: false, effective: false });
    expect(p.status).toBe('not_assigned');
    expect(p.statusLabel).toBe('Not assigned');
    expect(p.reason).toMatch(/not shown to this agent as guidance/i);
  });

  test('no provenance reason implies a permission / approval grant', () => {
    const rows = [
      { agent: 'a', assigned: true, effective: true, state: 'effective' },
      { agent: 'b', assigned: true, effective: false, state: 'assigned_not_yet_effective' },
      { agent: 'c', assigned: false, effective: false },
    ];
    for (const p of agentProvenanceList(rows)) {
      expect(p.reason).not.toMatch(/permission|approve|admit|grant|materialize now/i);
    }
  });
});

describe('skills-detail — rollup + edit route', () => {
  test('rollup counts assigned / effective / not-yet-effective', () => {
    const rows = [
      { agent: 'a', assigned: true, effective: true, state: 'effective' },
      { agent: 'b', assigned: true, effective: false, state: 'assigned_not_yet_effective' },
      { agent: 'c', assigned: true, effective: false, state: 'assigned_not_yet_effective' },
      { agent: 'd', assigned: false, effective: false },
    ];
    expect(assignmentRollup(rows)).toEqual({
      assigned: 3,
      effective: 1,
      notYetEffective: 2,
    });
  });

  test('editRoutePath targets the Slice-4 edit screen', () => {
    expect(editRoutePath('alpha', 'sk-x')).toBe('/orgs/alpha/skills/sk-x/edit');
  });
});
