import { describe, expect, it } from 'vitest';
import type { AuditEntry } from '@/lib/api/types';
import { describeAuditEntry, narrativeText } from './audit-narrative';

/* Build an AuditEntry with sensible defaults; override per case. */
function entry(over: Partial<AuditEntry>): AuditEntry {
  return {
    id: 1,
    task_id: null,
    session_id: null,
    agent: null,
    action: 'unknown',
    payload: {},
    timestamp: '2026-06-21T10:00:00+00:00',
    ...over,
  };
}

/* The authoritative set of audit `action` values emitted by
 * runtime/infrastructure/audit_logger.py (+ agents route). Kept independent of
 * the implementation so the coverage test catches a missing case. */
const KNOWN_ACTIONS: string[] = [
  'session_start',
  'session_end',
  'completion_report',
  'review_verdict',
  'escalation',
  'daemon_restart_failure',
  'escalation_resolved',
  'task_cancelled',
  'progress',
  'auto_revisit_of',
  'orchestration_step',
  'chain_auto_advance',
  'task_blocked_on_jobs',
  'task_resumed_from_jobs',
  'task_resume_skipped',
  'revisit_of',
  'revisit_spawned',
  'escalation_superseded',
  'artifact_put',
  'artifact_delete',
  'agent_managed',
  'agent_backfilled',
  'learning_added',
  'learning_updated',
  'learning_promoted',
  'thread_started',
  'thread_message_sent',
  'thread_decline_consumed',
  'thread_participant_added',
  'thread_dispatch',
  'agent_session_reused',
  'agent_session_evicted_fallback',
  'thread_task_followup_enqueued',
  'thread_followup_skipped',
  'thread_turn_cap_auto_extended',
  'thread_archived',
  'thread_resumed',
  'thread_invocation_failed',
  'job_submitted',
  'job_rejected',
  'job_run_started',
  'job_auto_started',
  'job_run_completed',
  'job_run_failed',
  'job_stopped',
  'dream_scheduled',
  'dream_started',
  'dream_completed',
  'dream_failed',
  'dream_timeout',
  'dream_founder_thread_created',
  'work_hour_scheduled',
  'work_hour_started',
  'work_hour_spawned',
  'work_hour_completed',
  'work_hour_failed',
  'work_hour_timeout',
];

describe('describeAuditEntry — coverage (one case per event type)', () => {
  it.each(KNOWN_ACTIONS)('%s renders a human sentence, not the raw code', (action) => {
    const n = describeAuditEntry(
      entry({ action, agent: 'dev_agent', task_id: 'TASK-408' }),
    );
    const text = narrativeText(n);
    // Never surfaces the raw snake_case event code verbatim as the sentence.
    expect(text).not.toBe(action);
    expect(text).not.toMatch(/^[a-z]+(_[a-z]+)+$/);
    // Always begins with the subject agent.
    expect(n.segments[0]).toEqual({ kind: 'subject', text: 'dev_agent' });
    expect(text.length).toBeGreaterThan('dev_agent'.length);
  });

  it('falls back to a humanized phrase for an unknown event type', () => {
    const n = describeAuditEntry(
      entry({ action: 'some_brand_new_event', agent: 'dev_agent', task_id: 'TASK-1' }),
    );
    const text = narrativeText(n);
    expect(text).toContain('some brand new event');
    // The raw snake_case event code is never surfaced verbatim.
    expect(text).not.toContain('some_brand_new_event');
    expect(n.segments[0]).toEqual({ kind: 'subject', text: 'dev_agent' });
  });

  it('uses a neutral subject when the actor is unknown', () => {
    const n = describeAuditEntry(entry({ action: 'session_start', agent: null }));
    expect(n.segments[0]).toEqual({ kind: 'subject', text: 'The system' });
  });
});

describe('describeAuditEntry — headline narratives', () => {
  it('completion_report', () => {
    const n = describeAuditEntry(
      entry({
        action: 'completion_report',
        agent: 'dev_agent',
        task_id: 'TASK-408',
        payload: { status: 'completed', confidence: 90 },
      }),
    );
    expect(narrativeText(n)).toBe('dev_agent completed TASK-408.');
    expect(n.segments).toContainEqual({
      kind: 'ref',
      ref: { type: 'task', id: 'TASK-408', label: 'TASK-408' },
    });
    expect(n.detail).toBe('completed · confidence 90');
  });

  it('thread_dispatch links the dispatched task and the target agent', () => {
    const n = describeAuditEntry(
      entry({
        action: 'thread_dispatch',
        agent: 'engineering_manager',
        task_id: 'THR-020',
        payload: { task_id: 'TASK-410', target_agent: 'dev_agent', team: 'engineering' },
      }),
    );
    expect(narrativeText(n)).toBe('engineering_manager dispatched TASK-410 to dev_agent.');
    expect(n.segments).toContainEqual({
      kind: 'ref',
      ref: { type: 'task', id: 'TASK-410', label: 'TASK-410' },
    });
    expect(n.segments).toContainEqual({
      kind: 'ref',
      ref: { type: 'agent', id: 'dev_agent', label: 'dev_agent' },
    });
  });

  it('review_verdict', () => {
    const n = describeAuditEntry(
      entry({
        action: 'review_verdict',
        agent: 'code_reviewer',
        task_id: 'TASK-689',
        payload: { verdict: 'APPROVE', feedback: null },
      }),
    );
    expect(narrativeText(n)).toBe('code_reviewer reviewed TASK-689.');
    expect(n.detail).toBe('APPROVE');
  });

  it('escalation carries the reason in the detail line', () => {
    const n = describeAuditEntry(
      entry({
        action: 'escalation',
        agent: 'engineering_manager',
        task_id: 'TASK-101',
        payload: { reason: 'blocks PR #101' },
      }),
    );
    expect(narrativeText(n)).toBe('engineering_manager escalated TASK-101.');
    expect(n.detail).toBe('blocks PR #101');
  });

  it('session_end reports duration and tokens', () => {
    const n = describeAuditEntry(
      entry({
        action: 'session_end',
        agent: 'dev_agent',
        task_id: 'TASK-678',
        payload: { duration_seconds: 80, token_usage: { total: 76300 }, token_count: 76300 },
      }),
    );
    expect(narrativeText(n)).toBe('dev_agent wrapped up TASK-678.');
    expect(n.detail).toBe('1m 20s · 76.3K tokens');
  });

  it('job_submitted links the job and its parent task', () => {
    const n = describeAuditEntry(
      entry({
        action: 'job_submitted',
        agent: 'dev_agent',
        task_id: 'TASK-680',
        payload: { script_request_id: 'JOB-083', title: 'run prod build', interpreter: 'bash' },
      }),
    );
    expect(narrativeText(n)).toBe('dev_agent submitted JOB-083 on TASK-680.');
    expect(n.segments).toContainEqual({
      kind: 'ref',
      ref: { type: 'job', id: 'JOB-083', label: 'JOB-083' },
    });
    expect(n.detail).toBe('run prod build');
  });

  it('artifact_put names the artifact from the payload (scope id is namespaced)', () => {
    const n = describeAuditEntry(
      entry({
        action: 'artifact_put',
        agent: 'qa_engineer',
        task_id: 'artifact:qa/report.png',
        payload: { name: 'qa/report.png', size_bytes: 2048 },
      }),
    );
    expect(narrativeText(n)).toBe('qa_engineer published qa/report.png.');
    expect(n.detail).toBe('2.0 KB');
    // The namespaced artifact: scope id must never become a (broken) task link.
    expect(n.segments).not.toContainEqual(
      expect.objectContaining({ kind: 'ref' }),
    );
  });

  it('progress', () => {
    const n = describeAuditEntry(
      entry({
        action: 'progress',
        agent: 'dev_agent',
        task_id: 'TASK-690',
        payload: { message: 'phase 3 of 6' },
      }),
    );
    expect(narrativeText(n)).toBe('dev_agent reported progress on TASK-690.');
    expect(n.detail).toBe('phase 3 of 6');
  });

  it('orchestration_step', () => {
    const n = describeAuditEntry(
      entry({
        action: 'orchestration_step',
        agent: 'orchestrator',
        task_id: 'TASK-688',
        payload: { step_number: 2, decision: { action: 'delegate' } },
      }),
    );
    expect(narrativeText(n)).toBe('orchestrator advanced TASK-688.');
    expect(n.detail).toBe('step 2 · delegate');
  });

  it('agent_managed renders the management verb and links the managed agent', () => {
    const n = describeAuditEntry(
      entry({
        action: 'agent_managed',
        agent: 'engineering_manager',
        task_id: 'TASK-1',
        payload: { action: 'enroll', name: 'new_agent', source: 'task' },
      }),
    );
    expect(narrativeText(n)).toBe('engineering_manager enrolled agent new_agent.');
    expect(n.segments).toContainEqual({
      kind: 'ref',
      ref: { type: 'agent', id: 'new_agent', label: 'new_agent' },
    });
  });

  it('thread_message_sent', () => {
    const n = describeAuditEntry(
      entry({
        action: 'thread_message_sent',
        agent: 'founder',
        task_id: 'THR-9',
        payload: { seq: 3, kind: 'reply' },
      }),
    );
    expect(narrativeText(n)).toBe('founder sent a message in THR-9.');
    expect(n.segments).toContainEqual({
      kind: 'ref',
      ref: { type: 'thread', id: 'THR-9', label: 'THR-9' },
    });
  });

  it('dream_completed', () => {
    const n = describeAuditEntry(
      entry({
        action: 'dream_completed',
        agent: 'scheduler',
        task_id: 'DREAM-1',
        payload: { new_learnings_count: 3, kb_candidate_count: 2, founder_thread_id: null },
      }),
    );
    expect(narrativeText(n)).toBe('scheduler completed a dream.');
    expect(n.detail).toBe('3 learnings · 2 KB candidates');
  });
});
