import { describe, expect, test } from 'vitest';
import type { CatalogSkillItem } from '@/hooks/skills';
import {
  applyFilter,
  isBundled,
  isReadOnly,
  matchesFilter,
  needsAttentionCount,
  sourceLabel,
  validationLabel,
} from './skills-catalog';

function skill(overrides: Partial<CatalogSkillItem> = {}): CatalogSkillItem {
  return {
    skill_id: 'sk-1',
    name: 'demo-skill',
    type: 'managed',
    source: 'bundled',
    system_contract: false,
    visibility_category: 'toggleable',
    policy_class: 'guidance',
    status: 'active',
    version: '1.0.0',
    validation_state: 'validated',
    assigned_agent_count: 0,
    effective_agent_count: 0,
    has_assigned_not_yet_effective: false,
    summary: 'A demo skill.',
    ...overrides,
  };
}

describe('source bucketing', () => {
  test('managed and system_contract are Bundled; user_authored is Custom', () => {
    expect(isBundled(skill({ type: 'managed' }))).toBe(true);
    expect(isBundled(skill({ type: 'system_contract' }))).toBe(true);
    expect(isBundled(skill({ type: 'user_authored' }))).toBe(false);
  });

  test('sourceLabel is lowercase bundled/custom', () => {
    expect(sourceLabel(skill({ type: 'system_contract' }))).toBe('bundled');
    expect(sourceLabel(skill({ type: 'user_authored' }))).toBe('custom');
  });
});

describe('matchesFilter / applyFilter — Bundled|Custom map to the filter param', () => {
  const items = [
    skill({ skill_id: 'a', type: 'system_contract' }),
    skill({ skill_id: 'b', type: 'managed' }),
    skill({ skill_id: 'c', type: 'user_authored' }),
  ];

  test('all passes everything through', () => {
    expect(applyFilter(items, 'all')).toHaveLength(3);
  });

  test('Bundled keeps managed + system_contract only', () => {
    expect(applyFilter(items, 'Bundled').map((i) => i.skill_id)).toEqual([
      'a',
      'b',
    ]);
  });

  test('Custom keeps user_authored only', () => {
    expect(applyFilter(items, 'Custom').map((i) => i.skill_id)).toEqual(['c']);
  });

  test('matchesFilter is the single predicate behind applyFilter', () => {
    expect(matchesFilter(skill({ type: 'user_authored' }), 'Custom')).toBe(true);
    expect(matchesFilter(skill({ type: 'managed' }), 'Custom')).toBe(false);
  });
});

describe('isReadOnly — system contracts are non-toggleable', () => {
  test('system_contract flag makes a skill read-only', () => {
    expect(isReadOnly(skill({ system_contract: true }))).toBe(true);
  });

  test('read_only visibility_category makes a skill read-only', () => {
    expect(isReadOnly(skill({ visibility_category: 'read_only' }))).toBe(true);
  });

  test('a toggleable custom skill is not read-only', () => {
    expect(
      isReadOnly(skill({ type: 'user_authored', visibility_category: 'toggleable' })),
    ).toBe(false);
  });
});

describe('validationLabel — product language, not permission wording', () => {
  test('validated → Validated (positive)', () => {
    expect(validationLabel('validated')).toEqual({
      text: 'Validated',
      tone: 'positive',
    });
  });

  test('failed_validation → Needs attention (attention)', () => {
    expect(validationLabel('failed_validation')).toEqual({
      text: 'Needs attention',
      tone: 'attention',
    });
  });

  test('in_catalog → In catalog (neutral)', () => {
    expect(validationLabel('in_catalog')).toEqual({
      text: 'In catalog',
      tone: 'neutral',
    });
  });

  test('no label uses forbidden "active"/"pending"/permission words', () => {
    const words = ['validated', 'failed_validation', 'in_catalog'] as const;
    for (const s of words) {
      const t = validationLabel(s).text.toLowerCase();
      expect(t).not.toContain('active');
      expect(t).not.toContain('pending');
      expect(t).not.toContain('approve');
      expect(t).not.toContain('permission');
    }
  });
});

describe('needsAttentionCount — failed validations only', () => {
  test('counts failed_validation rows, ignores not-yet-effective', () => {
    const items = [
      skill({ validation_state: 'validated' }),
      skill({ validation_state: 'failed_validation' }),
      skill({
        validation_state: 'validated',
        has_assigned_not_yet_effective: true,
      }),
    ];
    expect(needsAttentionCount(items)).toBe(1);
  });
});
