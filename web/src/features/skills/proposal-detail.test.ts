import { describe, expect, test } from 'vitest';
import {
  assignmentProjection,
  hasAssignmentProjection,
  hashDisplay,
  isPublished,
  isRejected,
  isTerminal,
  materializationProjection,
  metadataFacts,
  readinessFacts,
  statusLabel,
  statusTone,
  timelineEvents,
  validatorFacts,
} from './proposal-detail';
import type { ProposalDetailResponse } from '@/hooks/skills';

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
  test('proposed: not in catalog, no assignments/mats recorded, no reviewer decision', () => {
    const facts = readinessFacts(baseProposal({ status: 'proposed' }));
    const labels = facts.map((f) => f.label);
    expect(labels).toContain('Not in catalog');
    expect(labels).toContain('No assignments recorded');
    expect(labels).toContain('No materializations recorded');
    // No review_decision → no "Approved by reviewer" or "Rejected" fact
    expect(labels).not.toContain('Approved by reviewer');
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

  test('published with assignments and materializations shows both', () => {
    const facts = readinessFacts(
      baseProposal({
        status: 'published',
        assignments: [
          { agent_name: 'frontend_engineer', active: true },
          { agent_name: 'qa_engineer', active: true },
        ],
        materializations: [
          { agent_name: 'frontend_engineer', success: true, created_at: '2026-08-02' },
        ],
      }),
    );
    const labels = facts.map((f) => f.label);
    expect(labels).toContain('2 agent(s) assigned');
    expect(labels).toContain('1/1 materialization(s) succeeded');
  });

  test('rejected with review_decision shows decision fact', () => {
    const facts = readinessFacts(
      baseProposal({ status: 'rejected', review_decision: 'rejected' }),
    );
    const labels = facts.map((f) => f.label);
    expect(labels).toContain('Not in catalog');
    expect(labels).toContain('Rejected — terminal');
  });

  test('approved with review_decision shows approved fact', () => {
    const facts = readinessFacts(
      baseProposal({ status: 'approved', review_decision: 'approved' }),
    );
    const labels = facts.map((f) => f.label);
    expect(labels).toContain('Approved by reviewer');
    expect(labels).not.toContain('In custom catalog');
  });

  test('materialization with partial success shows warning count', () => {
    const facts = readinessFacts(
      baseProposal({
        status: 'published',
        materializations: [
          { agent_name: 'a', success: true },
          { agent_name: 'b', success: false, error_message: 'disk full' },
        ],
      }),
    );
    expect(facts.map((f) => f.label)).toContain('1/2 materialization(s) succeeded');
    const matFact = facts.find((f) => f.label.includes('materialization'));
    expect(matFact?.status).toBe('warning');
  });

  test('no review_decision → no reviewer approval/rejection fact', () => {
    const facts = readinessFacts(baseProposal({ status: 'validated', review_decision: null }));
    const labels = facts.map((f) => f.label);
    expect(labels).not.toContain('Approved by reviewer');
    expect(labels).not.toContain('Rejected — terminal');
  });
});

// ── timelineEvents ───────────────────────────────────────────────────────

describe('timelineEvents', () => {
  test('maps and labels events with actor/role/time', () => {
    const events = timelineEvents([
      { event_type: 'proposed', actor: 'frontend_engineer', actor_role: 'agent', new_status: 'proposed', created_at: '2026-08-01T09:00:00Z' },
      { event_type: 'validated', actor: 'founder', actor_role: 'founder', new_status: 'validated', created_at: '2026-08-02T10:00:00Z' },
    ]);
    expect(events).toHaveLength(2);
    expect(events[0].label).toBe('Proposed');
    expect(events[0].actor).toBe('frontend_engineer');
    expect(events[0].actorRole).toBe('agent');
    expect(events[0].time).toBe('2026-08-01T09:00:00Z');
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

  test('extracts content_hash from events where supplied', () => {
    const events = timelineEvents([
      {
        event_type: 'published',
        actor: 'founder',
        actor_role: 'founder',
        new_status: 'published',
        created_at: '2026-08-03T12:00:00Z',
        content_hash: 'sha256:abc123def456',
      },
    ]);
    expect(events[0].contentHash).toBe('sha256:abc123def456');
  });

  test('content_hash is null when not supplied', () => {
    const events = timelineEvents([
      {
        event_type: 'proposed',
        actor: 'agent',
        actor_role: 'agent',
        new_status: 'proposed',
        created_at: '2026-08-01T09:00:00Z',
      },
    ]);
    expect(events[0].contentHash).toBeNull();
  });

  test('extracts metadata facts from validation event', () => {
    const events = timelineEvents([
      {
        event_type: 'validated',
        actor: 'founder',
        actor_role: 'founder',
        new_status: 'validated',
        created_at: '2026-08-02T10:00:00Z',
        metadata: {
          validator_version: 'THR-055/1.0.0',
          validator_key: 'hr-thr055',
          run_id: 'run-42',
        },
      },
    ]);
    const facts = events[0].metadataFacts;
    expect(facts.map((f) => f.key)).toContain('Validator version');
    expect(facts.map((f) => f.key)).toContain('Validator key');
    expect(facts.map((f) => f.key)).toContain('Run');
  });

  test('extracts rationale and failure from review events', () => {
    const events = timelineEvents([
      {
        event_type: 'rejected',
        actor: 'founder',
        actor_role: 'founder',
        new_status: 'rejected',
        created_at: '2026-08-03T12:00:00Z',
        metadata: {
          rationale: 'Does not meet quality bar',
          failure: 'Missing required references',
        },
      },
    ]);
    const facts = events[0].metadataFacts;
    const keys = facts.map((f) => f.key);
    expect(keys).toContain('Rationale');
    expect(keys).toContain('Failure');
    expect(facts.find((f) => f.key === 'Rationale')?.value).toBe('Does not meet quality bar');
  });

  test('metadata facts are empty when metadata is null', () => {
    const events = timelineEvents([
      {
        event_type: 'proposed',
        actor: 'agent',
        actor_role: 'agent',
        created_at: '2026-08-01',
        metadata: null,
      },
    ]);
    expect(events[0].metadataFacts).toHaveLength(0);
  });

  test('does not include unknown/irrelevant metadata keys', () => {
    const events = timelineEvents([
      {
        event_type: 'proposed',
        actor: 'agent',
        actor_role: 'agent',
        created_at: '2026-08-01',
        metadata: { unknown_key: 'should-not-appear', _internal: 'nope' },
      },
    ]);
    const keys = events[0].metadataFacts.map((f) => f.key);
    expect(keys).not.toContain('unknown_key');
  });
});

// ── metadataFacts ────────────────────────────────────────────────────────

describe('metadataFacts', () => {
  test('returns empty for null metadata', () => {
    expect(metadataFacts(null)).toHaveLength(0);
  });

  test('returns empty for empty object', () => {
    expect(metadataFacts({})).toHaveLength(0);
  });

  test('extracts known keys only', () => {
    const facts = metadataFacts({
      validator_version: 'v1',
      run_id: 'abc',
      unknown_field: 'hidden',
    });
    const keys = facts.map((f) => f.key);
    expect(keys).toContain('Validator version');
    expect(keys).toContain('Run');
    expect(keys).not.toContain('unknown_field');
  });

  test('skips empty-string values', () => {
    const facts = metadataFacts({ validator_version: '', validator_key: null });
    expect(facts).toHaveLength(0);
  });

  test('maps known keys to display labels', () => {
    const facts = metadataFacts({
      validator_version: 'THR-055/1.0.0',
      validator_key: 'hr-thr055',
      reason: 'Rollback requested',
      rationale: 'Quality check failed',
      failure: 'Disk full',
      error: 'Connection timeout',
    });
    const map = Object.fromEntries(facts.map((f) => [f.key, f.value]));
    expect(map['Validator version']).toBe('THR-055/1.0.0');
    expect(map['Validator key']).toBe('hr-thr055');
    expect(map['Reason']).toBe('Rollback requested');
    expect(map['Rationale']).toBe('Quality check failed');
    expect(map['Failure']).toBe('Disk full');
    expect(map['Error']).toBe('Connection timeout');
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

// ── materializationProjection ────────────────────────────────────────────

describe('materializationProjection', () => {
  test('maps success=true materialization', () => {
    const items = materializationProjection([
      { agent_name: 'frontend_engineer', success: true, created_at: '2026-08-02T10:00:00Z' },
    ]);
    expect(items).toHaveLength(1);
    expect(items[0].agentName).toBe('frontend_engineer');
    expect(items[0].success).toBe(true);
    expect(items[0].createdAt).toBe('2026-08-02T10:00:00Z');
    expect(items[0].errorMessage).toBeNull();
  });

  test('maps success=false with error_message', () => {
    const items = materializationProjection([
      {
        agent_name: 'qa_engineer',
        success: false,
        error_message: 'Disk quota exceeded',
        created_at: '2026-08-03T09:00:00Z',
      },
    ]);
    expect(items[0].success).toBe(false);
    expect(items[0].errorMessage).toBe('Disk quota exceeded');
  });

  test('success is null when not a boolean', () => {
    const items = materializationProjection([
      { agent_name: 'agent', success: 'not-a-bool' },
    ]);
    expect(items[0].success).toBeNull();
  });

  test('never reads materialized_at field', () => {
    const items = materializationProjection([
      { agent_name: 'agent', materialized_at: '2026-01-01', success: true, created_at: '2026-08-01' },
    ]);
    // createdAt comes from created_at, not materialized_at
    expect(items[0].createdAt).toBe('2026-08-01');
  });

  test('empty array → empty', () => {
    expect(materializationProjection([])).toHaveLength(0);
  });
});
