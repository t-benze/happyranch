import { describe, expect, test } from 'vitest';
import {
  assignmentProjection,
  hasAssignmentProjection,
  hashDisplay,
  isPublished,
  isRejected,
  isTerminal,
  readinessFacts,
  statusLabel,
  statusTone,
  timelineEvents,
  validatorFacts,
} from './proposal-detail';
import type { ProposalDetailResponse } from '@/lib/api/skillLifecycle';

function baseProposal(overrides: Partial<ProposalDetailResponse> = {}): ProposalDetailResponse {
  return {
    version_id: 1,
    skill_id: 'hr:test-skill',
    slug: 'test-skill',
    name: 'Test Skill',
    version: '0.1.0',
    description: 'A test skill proposal.',
    content_hash: 'sha256:53cb67fc7ead400a400fbcede2f5371c69747d065dfff9304b0c289b27367328',
    content_artifact_key: 'skill-lifecycle/test-skill/abc123/manifest.json',
    policy_class: 'standard_operational',
    status: 'proposed',
    proposer_agent: 'frontend_engineer',
    proposal_task_id: 'TASK-3864',
    proposal_session_id: 'sess-8091',
    claimed_by: null,
    claimed_at: null,
    reviewer: null,
    review_decision: null,
    review_rationale: null,
    reviewed_at: null,
    publisher: null,
    published_at: null,
    purpose: 'Implement & review frontend changes',
    target_agent_suggestion: 'frontend_engineer',
    skill_md: '# Test Skill\n\nTest content.',
    package_members: null,
    events: [
      {
        event_type: 'proposed',
        actor: 'frontend_engineer',
        actor_role: 'agent',
        new_status: 'proposed',
        previous_status: null,
        content_hash: null,
        created_at: '2026-08-01T09:14:00Z',
        metadata: null,
      },
    ],
    assignments: [],
    materializations: [],
    last_event_id: 1,
    created_at: '2026-08-01T09:14:00Z',
    ...overrides,
  };
}

// ── statusLabel ──────────────────────────────────────────────────────────

describe('statusLabel', () => {
  test.each([
    ['proposed', 'Proposed'],
    ['draft', 'Draft'],
    ['validation_failed', 'Validation failed'],
    ['validated', 'Validated'],
    ['in_review', 'In review'],
    ['approved', 'Approved'],
    ['published', 'Published'],
    ['rejected', 'Rejected'],
    ['legacy_quarantined', 'Legacy quarantined'],
  ] as const)('%s → "%s"', (status, expected) => {
    expect(statusLabel(status)).toBe(expected);
  });

  test('unknown status returns title-case raw value', () => {
    expect(statusLabel('custom_state')).toBe('Custom_state');
  });
});

// ── statusTone ───────────────────────────────────────────────────────────

describe('statusTone', () => {
  test('proposed → pending', () => {
    expect(statusTone('proposed')).toBe('pending');
  });
  test('published → done', () => {
    expect(statusTone('published')).toBe('done');
  });
  test('rejected → failed', () => {
    expect(statusTone('rejected')).toBe('failed');
  });
  test('unknown → unknown', () => {
    expect(statusTone('bogus')).toBe('unknown');
  });
});

// ── Terminal predicates ──────────────────────────────────────────────────

describe('isRejected', () => {
  test('rejected is true', () => {
    expect(isRejected('rejected')).toBe(true);
  });
  test('published is false', () => {
    expect(isRejected('published')).toBe(false);
  });
});

describe('isPublished', () => {
  test('published is true', () => {
    expect(isPublished('published')).toBe(true);
  });
  test('rejected is false', () => {
    expect(isPublished('rejected')).toBe(false);
  });
});

describe('isTerminal', () => {
  test('published is terminal', () => {
    expect(isTerminal('published')).toBe(true);
  });
  test('rejected is terminal', () => {
    expect(isTerminal('rejected')).toBe(true);
  });
  test('proposed is not terminal', () => {
    expect(isTerminal('proposed')).toBe(false);
  });
});

// ── hashDisplay ──────────────────────────────────────────────────────────

describe('hashDisplay', () => {
  test('formats sha256-prefixed hash', () => {
    const h = hashDisplay(
      'sha256:53cb67fc7ead400a400fbcede2f5371c69747d065dfff9304b0c289b27367328',
    );
    expect(h.full).toBe(
      'sha256:53cb67fc7ead400a400fbcede2f5371c69747d065dfff9304b0c289b27367328',
    );
    expect(h.short).toBe('53cb67fc7e…67328');
  });

  test('handles hex-only hash (no prefix)', () => {
    const h = hashDisplay('abc123def456');
    expect(h.full).toBe('sha256:abc123def456');
    expect(h.short).toBe('abc123def456');
  });

  test('short hash for content shorter than 14 chars', () => {
    const h = hashDisplay('abc');
    expect(h.short).toBe('abc');
  });
});

// ── readinessFacts ───────────────────────────────────────────────────────

describe('readinessFacts', () => {
  test('proposed: not in catalog, not assigned, not materialized', () => {
    const facts = readinessFacts(baseProposal({ status: 'proposed' }));
    const labels = facts.map((f) => f.label);
    expect(labels).toContain('Not in catalog');
    expect(labels).toContain('Not assigned');
    expect(labels).toContain('Not materialized');
  });

  test('published: in custom catalog', () => {
    const facts = readinessFacts(baseProposal({ status: 'published' }));
    expect(facts.map((f) => f.label)).toContain('In custom catalog');
  });

  test('published with assignments shows count', () => {
    const facts = readinessFacts(
      baseProposal({
        status: 'published',
        assignments: [
          { agent_name: 'frontend_engineer', active: true },
        ],
      }),
    );
    expect(facts.map((f) => f.label)).toContain('1 agent(s) assigned');
  });

  test('rejected: terminal, not in catalog', () => {
    const facts = readinessFacts(baseProposal({ status: 'rejected' }));
    expect(facts.map((f) => f.label)).toContain('Terminal — no further action');
    expect(facts.map((f) => f.label)).toContain('Not in catalog');
  });

  test('validation_failed: failed + not in catalog', () => {
    const facts = readinessFacts(baseProposal({ status: 'validation_failed' }));
    expect(facts.map((f) => f.label)).toContain('Failed technical validation');
    expect(facts.map((f) => f.label)).toContain('Not in catalog');
  });
});

// ── timelineEvents ───────────────────────────────────────────────────────

describe('timelineEvents', () => {
  test('maps and labels events', () => {
    const events = timelineEvents([
      { event_type: 'proposed', actor: 'frontend_engineer', actor_role: 'agent', new_status: 'proposed', created_at: '2026-08-01T09:00:00Z' },
      { event_type: 'validated', actor: 'founder', actor_role: 'founder', new_status: 'validated', created_at: '2026-08-02T10:00:00Z' },
    ]);
    expect(events).toHaveLength(2);
    expect(events[0].label).toBe('Proposed');
    expect(events[1].label).toBe('Validation passed');
  });

  test('sorts by created_at ascending', () => {
    const events = timelineEvents([
      { event_type: 'validated', actor: 'founder', actor_role: 'founder', new_status: 'validated', created_at: '2026-08-02T10:00:00Z' },
      { event_type: 'proposed', actor: 'frontend_engineer', actor_role: 'agent', new_status: 'proposed', created_at: '2026-08-01T09:00:00Z' },
    ]);
    expect(events[0].eventType).toBe('proposed');
    expect(events[1].eventType).toBe('validated');
  });

  test('empty events → empty array', () => {
    expect(timelineEvents([])).toHaveLength(0);
  });
});

// ── validatorFacts ───────────────────────────────────────────────────────

describe('validatorFacts', () => {
  test('no validation events → hasValidation=false', () => {
    const v = validatorFacts([
      { event_type: 'proposed', actor: 'agent', actor_role: 'agent' },
    ]);
    expect(v.hasValidation).toBe(false);
    expect(v.result).toBeNull();
  });

  test('validated event → Passed', () => {
    const v = validatorFacts([
      { event_type: 'proposed', actor: 'agent', actor_role: 'agent' },
      {
        event_type: 'validated',
        actor: 'founder',
        actor_role: 'founder',
        metadata: { validator_version: 'THR-055/1.0.0', validator_key: 'hr-thr055' },
      },
    ]);
    expect(v.hasValidation).toBe(true);
    expect(v.result).toBe('Passed');
    expect(v.version).toBe('THR-055/1.0.0');
    expect(v.key).toBe('hr-thr055');
  });

  test('validation_failed event → Failed', () => {
    const v = validatorFacts([
      { event_type: 'validation_failed', actor: 'founder', actor_role: 'founder' },
    ]);
    expect(v.hasValidation).toBe(true);
    expect(v.result).toBe('Failed');
  });

  test('latest event wins (reversed search)', () => {
    const v = validatorFacts([
      { event_type: 'validation_failed', actor: 'founder', actor_role: 'founder', created_at: '2026-08-01' },
      { event_type: 'validated', actor: 'founder', actor_role: 'founder', created_at: '2026-08-02' },
    ]);
    expect(v.result).toBe('Passed');
  });
});

// ── assignmentProjection ─────────────────────────────────────────────────

describe('assignmentProjection', () => {
  test('maps assignments', () => {
    const proj = assignmentProjection([
      { agent_name: 'frontend_engineer', active: true, version: '0.1.0', assigned_by: 'founder', assigned_at: '2026-08-03' },
    ]);
    expect(proj).toHaveLength(1);
    expect(proj[0].agentName).toBe('frontend_engineer');
    expect(proj[0].assigned).toBe(true);
    expect(proj[0].version).toBe('0.1.0');
  });

  test('empty array → empty', () => {
    expect(assignmentProjection([])).toHaveLength(0);
  });
});

// ── hasAssignmentProjection ──────────────────────────────────────────────

describe('hasAssignmentProjection', () => {
  test('no assignments or materializations → false', () => {
    expect(hasAssignmentProjection(baseProposal())).toBe(false);
  });

  test('has assignments → true', () => {
    expect(
      hasAssignmentProjection(
        baseProposal({
          assignments: [{ agent_name: 'a', active: true }],
        }),
      ),
    ).toBe(true);
  });

  test('has materializations → true', () => {
    expect(
      hasAssignmentProjection(
        baseProposal({
          materializations: [{ agent_name: 'a' }],
        }),
      ),
    ).toBe(true);
  });
});
