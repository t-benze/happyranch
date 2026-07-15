import { describe, expect, test } from 'vitest';
import type { ValidationEvent } from '@/hooks/skills';
import {
  agentLabel,
  agentOptions,
  buildValidationQuery,
  EMPTY_FILTERS,
  formatEventTime,
  reasonCodeLabel,
  severityBadge,
  skillOptions,
  sourceLabel,
  toValidationRow,
  type ValidationFilters,
} from './skills-validation';

// A fixed "now" so relative-age assertions are deterministic.
const NOW = Date.parse('2026-07-15T12:00:00Z');

function ev(over: Partial<ValidationEvent> = {}): ValidationEvent {
  return {
    id: 1,
    skill_id: 'sk-refund-decision-guide',
    slug: 'refund-decision-guide',
    agent: 'support_agent',
    source: 'user_authored',
    severity: 'pass',
    ok: true,
    version: '1.0.0',
    findings: [],
    reason_codes: [],
    created_at: '2026-07-15T11:59:30Z',
    ...over,
  };
}

describe('severityBadge', () => {
  test('pass → positive "Passed"', () => {
    expect(severityBadge('pass')).toEqual({ text: 'Passed', tone: 'positive' });
  });
  test('error → attention "Needs attention" (product language, not permission)', () => {
    expect(severityBadge('error')).toEqual({
      text: 'Needs attention',
      tone: 'attention',
    });
  });
  test('warn/info map to product words', () => {
    expect(severityBadge('warn').text).toBe('Warning');
    expect(severityBadge('info')).toEqual({ text: 'Info', tone: 'neutral' });
  });
  test('unknown severity is humanized, never the raw enum', () => {
    const b = severityBadge('some_new_kind');
    expect(b.text).toBe('Some new kind');
    expect(b.tone).toBe('neutral');
  });
});

describe('reasonCodeLabel', () => {
  test('maps known technical codes to plain language', () => {
    expect(reasonCodeLabel('missing_version')).toBe(
      'The skill guide is missing a version.',
    );
    expect(reasonCodeLabel('slug_collision')).toBe(
      'This slug is already used by another skill.',
    );
  });
  test('materialization / contract-predicate codes avoid forbidden tokens', () => {
    const mat = reasonCodeLabel('materialization_error');
    const pred = reasonCodeLabel('contract_predicate_error');
    const next = reasonCodeLabel('next_session_materialization');
    for (const line of [mat, pred, next]) {
      expect(line).not.toMatch(/materializ|admit|permission|approve|grant|\bpending\b/i);
    }
    expect(next).toBe('Takes effect next session.');
  });
  test('unknown code is humanized with a period, never raw enum jargon', () => {
    expect(reasonCodeLabel('brand_new_reason')).toBe('Brand new reason.');
  });
});

describe('agentLabel', () => {
  test('named agent passes through', () => {
    expect(agentLabel('support_agent')).toBe('support_agent');
  });
  test('null agent → context-applied product label, never blank', () => {
    const label = agentLabel(null);
    expect(label).toBe('Applied by context — all agents');
    expect(label.trim().length).toBeGreaterThan(0);
  });
});

describe('sourceLabel', () => {
  test('maps the three real daemon event sources to product labels', () => {
    expect(sourceLabel('user_authored')).toBe('Custom');
    expect(sourceLabel('first_party')).toBe('Bundled');
    expect(sourceLabel('materialization')).toBe('Applied at session spawn');
  });
  test('the materialization label is copy-gate-safe (no forbidden token, no "active")', () => {
    const label = sourceLabel('materialization');
    expect(label).not.toMatch(/materializ|admit|permission|approve|grant|\bpending\b/i);
    expect(label).not.toMatch(/\bactive\b/i);
  });
  test('unknown source is humanized, never the raw enum', () => {
    expect(sourceLabel('some_source')).toBe('Some source');
  });
});

describe('formatEventTime', () => {
  test('recent event → "just now"', () => {
    expect(formatEventTime('2026-07-15T11:59:30Z', NOW).relative).toBe('just now');
  });
  test('minutes / hours / days buckets', () => {
    expect(formatEventTime('2026-07-15T11:30:00Z', NOW).relative).toBe('30m');
    expect(formatEventTime('2026-07-15T09:00:00Z', NOW).relative).toBe('3h');
    expect(formatEventTime('2026-07-13T12:00:00Z', NOW).relative).toBe('2d');
  });
  test('absolute string is populated', () => {
    expect(formatEventTime('2026-07-15T09:00:00Z', NOW).absolute.length).toBeGreaterThan(0);
  });
});

describe('toValidationRow', () => {
  test('projects a full product-language row view-model', () => {
    const row = toValidationRow(
      ev({
        id: 7,
        severity: 'error',
        ok: false,
        agent: null,
        source: 'materialization',
        findings: ['SKILL.md is missing a required version field.'],
        reason_codes: ['missing_version', 'contract_predicate_error'],
      }),
      NOW,
    );
    expect(row.id).toBe(7);
    expect(row.skillName).toBe('refund-decision-guide');
    expect(row.agentLabel).toBe('Applied by context — all agents');
    expect(row.sourceLabel).toBe('Applied at session spawn');
    expect(row.severity).toEqual({ text: 'Needs attention', tone: 'attention' });
    expect(row.ok).toBe(false);
    expect(row.okLabel).toBe('Not passed');
    expect(row.findings).toEqual([
      'SKILL.md is missing a required version field.',
    ]);
    expect(row.reasonLines).toEqual([
      'The skill guide is missing a version.',
      'A system-contract rule could not be checked for this agent.',
    ]);
  });
  test('tolerates missing findings/reason_codes arrays', () => {
    const row = toValidationRow(
      { ...ev(), findings: undefined as never, reason_codes: undefined as never },
      NOW,
    );
    expect(row.findings).toEqual([]);
    expect(row.reasonLines).toEqual([]);
  });
});

describe('buildValidationQuery', () => {
  test('all filters at default → undefined (dedupes with the options query)', () => {
    expect(buildValidationQuery(EMPTY_FILTERS, NOW)).toBeUndefined();
  });
  test('skill/agent/source/severity map to their params', () => {
    const f: ValidationFilters = {
      skill: 'sk-1',
      agent: 'support_agent',
      source: 'user_authored',
      severity: 'error',
      time: 'all',
    };
    expect(buildValidationQuery(f, NOW)).toEqual({
      skill: 'sk-1',
      agent: 'support_agent',
      source: 'user_authored',
      severity: 'error',
    });
  });
  test('time window → an ISO `since` param', () => {
    const q = buildValidationQuery({ ...EMPTY_FILTERS, time: '24h' }, NOW);
    expect(q?.since).toBe(new Date(NOW - 24 * 3600 * 1000).toISOString());
    // no other params
    expect(Object.keys(q ?? {})).toEqual(['since']);
  });
});

describe('option derivation', () => {
  const events = [
    ev({ skill_id: 'sk-b', slug: 'beta-skill', agent: 'agent_two' }),
    ev({ skill_id: 'sk-a', slug: 'alpha-skill', agent: 'agent_one' }),
    ev({ skill_id: 'sk-a', slug: 'alpha-skill', agent: null }),
  ];
  test('skillOptions dedupes by skill_id, sorted by slug', () => {
    expect(skillOptions(events)).toEqual([
      { value: 'sk-a', label: 'alpha-skill' },
      { value: 'sk-b', label: 'beta-skill' },
    ]);
  });
  test('agentOptions excludes null agents and dedupes', () => {
    expect(agentOptions(events)).toEqual([
      { value: 'agent_one', label: 'agent_one' },
      { value: 'agent_two', label: 'agent_two' },
    ]);
  });
});
