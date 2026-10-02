import { describe, expect, it } from 'vitest';
import type { AuditEntry } from '@/lib/api/types';
import { translate, type MessageKey, type MessageParams } from '@/lib/i18n';
import { describeAuditEntry, narrativeText } from './audit-narrative';

/* English expectations come from the typed catalog (THR-118 W4b). */
const en = (key: MessageKey, params?: MessageParams): string => translate('en', key, params);
const zh = (key: MessageKey, params?: MessageParams): string => translate('zh-CN', key, params);

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
  'thread_renamed',
  'thread_pinned',
  'thread_unpinned',
  'thread_reply_wake_created',
  'thread_reply_wake_coalesced',
  'thread_reply_wake_claimed',
  'thread_reply_wake_settled',
  'thread_reply_wake_cancelled',
  'thread_reply_wake_recovered',
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
      'en',
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
      'en',
    );
    const text = narrativeText(n);
    expect(text).toContain('some brand new event');
    // The raw snake_case event code is never surfaced verbatim.
    expect(text).not.toContain('some_brand_new_event');
    expect(n.segments[0]).toEqual({ kind: 'subject', text: 'dev_agent' });
  });

  it('uses a neutral subject when the actor is unknown', () => {
    const n = describeAuditEntry(entry({ action: 'session_start', agent: null }), 'en');
    expect(n.segments[0]).toEqual({ kind: 'subject', text: en('audit.n.subject.system') });
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
      'en',
    );
    expect(narrativeText(n)).toBe(en('audit.n.completion_report', { agent: 'dev_agent', target: 'TASK-408' }));
    expect(n.segments).toContainEqual({
      kind: 'ref',
      ref: { type: 'task', id: 'TASK-408', label: 'TASK-408' },
    });
    expect(n.detail).toBe(`completed · ${en('audit.detail.confidence', { value: 90 })}`);
  });

  it('thread_dispatch links the dispatched task and the target agent', () => {
    const n = describeAuditEntry(
      entry({
        action: 'thread_dispatch',
        agent: 'engineering_manager',
        task_id: 'THR-020',
        payload: { task_id: 'TASK-410', target_agent: 'dev_agent', team: 'engineering' },
      }),
      'en',
    );
    expect(narrativeText(n)).toBe(
      en('audit.n.thread_dispatch.to', { agent: 'engineering_manager', task: 'TASK-410', who: 'dev_agent' }),
    );
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
      'en',
    );
    expect(narrativeText(n)).toBe(en('audit.n.review_verdict', { agent: 'code_reviewer', target: 'TASK-689' }));
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
      'en',
    );
    expect(narrativeText(n)).toBe(en('audit.n.escalation', { agent: 'engineering_manager', target: 'TASK-101' }));
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
      'en',
    );
    expect(narrativeText(n)).toBe(en('audit.n.session_end', { agent: 'dev_agent', target: 'TASK-678' }));
    expect(n.detail).toBe(
      `${en('audit.detail.minutesSeconds', { m: 1, s: 20 })} · ${en('audit.detail.tokens', { tokens: '76.3K' })}`,
    );
  });

  it('job_submitted links the job and its parent task', () => {
    const n = describeAuditEntry(
      entry({
        action: 'job_submitted',
        agent: 'dev_agent',
        task_id: 'TASK-680',
        payload: { script_request_id: 'JOB-083', title: 'run prod build', interpreter: 'bash' },
      }),
      'en',
    );
    expect(narrativeText(n)).toBe(
      en('audit.n.job_submitted.on', { agent: 'dev_agent', job: 'JOB-083', target: 'TASK-680' }),
    );
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
      'en',
    );
    expect(narrativeText(n)).toBe(en('audit.n.artifact_put', { agent: 'qa_engineer', name: 'qa/report.png' }));
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
      'en',
    );
    expect(narrativeText(n)).toBe(en('audit.n.progress', { agent: 'dev_agent', target: 'TASK-690' }));
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
      'en',
    );
    expect(narrativeText(n)).toBe(en('audit.n.orchestration_step', { agent: 'orchestrator', target: 'TASK-688' }));
    expect(n.detail).toBe(`${en('audit.detail.step', { step: 2 })} · delegate`);
  });

  it('agent_managed renders the management verb and links the managed agent', () => {
    const n = describeAuditEntry(
      entry({
        action: 'agent_managed',
        agent: 'engineering_manager',
        task_id: 'TASK-1',
        payload: { action: 'enroll', name: 'new_agent', source: 'task' },
      }),
      'en',
    );
    expect(narrativeText(n)).toBe(
      en('audit.n.agent_managed.enroll', { agent: 'engineering_manager', target: 'new_agent' }),
    );
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
      'en',
    );
    expect(narrativeText(n)).toBe(en('audit.n.thread_message_sent', { agent: 'founder', target: 'THR-9' }));
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
      'en',
    );
    expect(narrativeText(n)).toBe(en('audit.n.dream_completed', { agent: 'scheduler' }));
    expect(n.detail).toBe(
      `${en('audit.detail.learnings', { count: 3 })} · ${en('audit.detail.kbCandidates', { count: 2 })}`,
    );
  });
});

describe('thread reply delivery lifecycle (GH-688 Phase 1 Slice C)', () => {
  it('thread_reply_wake_created', () => {
    const n = describeAuditEntry(
      entry({
        action: 'thread_reply_wake_created',
        agent: 'dev_agent',
        task_id: 'THR-9',
        payload: {
          agent_name: 'dev_agent',
          from_seq: 2,
          through_seq: 4,
          token_prefix: 'abc12345',
        },
      }),
      'en',
    );
    expect(narrativeText(n)).toBe(en('audit.n.thread_reply_wake_created.in', { agent: 'dev_agent', target: 'THR-9' }));
    expect(n.detail).toBe(en('audit.detail.messages', { from: 2, to: 4 }));
    expect(n.segments).toContainEqual({
      kind: 'ref',
      ref: { type: 'thread', id: 'THR-9', label: 'THR-9' },
    });
  });

  it('thread_reply_wake_coalesced', () => {
    const n = describeAuditEntry(
      entry({
        action: 'thread_reply_wake_coalesced',
        agent: 'dev_agent',
        task_id: 'THR-9',
        payload: { agent_name: 'dev_agent', from_seq: 2, through_seq: 5 },
      }),
      'en',
    );
    expect(narrativeText(n)).toBe(
      en('audit.n.thread_reply_wake_coalesced.in', { agent: 'dev_agent', target: 'THR-9' }),
    );
    expect(n.detail).toBe(en('audit.detail.messages', { from: 2, to: 5 }));
  });

  it('thread_reply_wake_claimed', () => {
    const n = describeAuditEntry(
      entry({
        action: 'thread_reply_wake_claimed',
        agent: 'dev_agent',
        task_id: 'THR-9',
        payload: {
          agent_name: 'dev_agent',
          from_seq: 1,
          through_seq: 3,
          token_prefix: 'abc12345',
        },
      }),
      'en',
    );
    expect(narrativeText(n)).toBe(en('audit.n.thread_reply_wake_claimed.in', { agent: 'dev_agent', target: 'THR-9' }));
    expect(n.detail).toBe(en('audit.detail.messages', { from: 1, to: 3 }));
  });

  it('thread_reply_wake_settled with follow-on', () => {
    const n = describeAuditEntry(
      entry({
        action: 'thread_reply_wake_settled',
        agent: 'dev_agent',
        task_id: 'THR-9',
        payload: {
          agent_name: 'dev_agent',
          outcome: 'reply',
          acknowledged_through_seq: 3,
          required_through_seq: 4,
          retry_required: false,
          follow_on_token_prefix: 'def45678',
          decline_reason: null,
        },
      }),
      'en',
    );
    expect(narrativeText(n)).toBe(en('audit.n.thread_reply_wake_settled.in', { agent: 'dev_agent', target: 'THR-9' }));
    expect(n.detail).toBe(
      [
        'reply',
        en('audit.detail.acknowledgedThrough', { seq: 3 }),
        en('audit.detail.requiredThrough', { seq: 4 }),
        en('audit.detail.followOnMinted'),
      ].join(' · '),
    );
  });

  it('thread_reply_wake_settled failure surfaces retry required', () => {
    const n = describeAuditEntry(
      entry({
        action: 'thread_reply_wake_settled',
        agent: 'dev_agent',
        task_id: 'THR-9',
        payload: {
          agent_name: 'dev_agent',
          outcome: 'failed',
          acknowledged_through_seq: 1,
          required_through_seq: 4,
          retry_required: true,
          follow_on_token_prefix: null,
          decline_reason: 'timeout',
        },
      }),
      'en',
    );
    expect(narrativeText(n)).toBe(en('audit.n.thread_reply_wake_settled.in', { agent: 'dev_agent', target: 'THR-9' }));
    expect(n.detail).toBe(
      [
        'failed',
        en('audit.detail.acknowledgedThrough', { seq: 1 }),
        en('audit.detail.requiredThrough', { seq: 4 }),
        en('audit.detail.retryRequired'),
      ].join(' · '),
    );
  });

  it('thread_reply_wake_cancelled', () => {
    const n = describeAuditEntry(
      entry({
        action: 'thread_reply_wake_cancelled',
        agent: 'qa_engineer',
        task_id: 'THR-9',
        payload: {
          agent_name: 'qa_engineer',
          boundary_seq: 4,
          reason: 'founder_aborted',
          swept_count: 2,
        },
      }),
      'en',
    );
    expect(narrativeText(n)).toBe(
      en('audit.n.thread_reply_wake_cancelled.in', { agent: 'qa_engineer', target: 'THR-9' }),
    );
    expect(n.detail).toBe(
      [
        'founder_aborted',
        en('audit.detail.discardedThrough', { seq: 4 }),
        en('audit.detail.receiptsRetired', { count: 2 }),
      ].join(' · '),
    );
  });

  it('thread_reply_wake_recovered', () => {
    const n = describeAuditEntry(
      entry({
        action: 'thread_reply_wake_recovered',
        agent: 'dev_agent',
        task_id: 'THR-9',
        payload: {
          agent_name: 'dev_agent',
          kind: 'replacement_queued',
          from_seq: 1,
          through_seq: 3,
          token_prefix: 'abc12345',
        },
      }),
      'en',
    );
    expect(narrativeText(n)).toBe(
      en('audit.n.thread_reply_wake_recovered.in', { agent: 'dev_agent', target: 'THR-9' }),
    );
    expect(n.detail).toBe(`replacement_queued · ${en('audit.detail.messages', { from: 1, to: 3 })}`);
  });
});

describe('describeAuditEntry — zh-CN (THR-118 W4b)', () => {
  it.each(KNOWN_ACTIONS)('%s renders a zh-CN sentence led by the verbatim subject', (action) => {
    const n = describeAuditEntry(entry({ action, agent: 'dev_agent', task_id: 'TASK-408' }), 'zh-CN');
    const text = narrativeText(n);
    expect(n.segments[0]).toEqual({ kind: 'subject', text: 'dev_agent' });
    expect(text).not.toContain(action);
    // Every sentence is translated: it carries CJK prose and ends with the
    // Chinese full stop (or the closing parenthesis of a linked id).
    expect(text).toMatch(/[一-鿿]/);
    expect(text).toMatch(/[。）]$/);
    expect(text).not.toBe(narrativeText(describeAuditEntry(entry({ action, agent: 'dev_agent', task_id: 'TASK-408' }), 'en')));
  });

  it('keeps refs and verbatim payload values inside translated slots', () => {
    const n = describeAuditEntry(
      entry({
        action: 'thread_dispatch',
        agent: 'engineering_manager',
        task_id: 'THR-020',
        payload: { task_id: 'TASK-410', target_agent: 'dev_agent', team: 'engineering' },
      }),
      'zh-CN',
    );
    expect(narrativeText(n)).toBe(
      zh('audit.n.thread_dispatch.to', { agent: 'engineering_manager', task: 'TASK-410', who: 'dev_agent' }),
    );
    expect(narrativeText(n)).toBe('engineering_manager 将 TASK-410 派发给 dev_agent。');
    expect(n.segments).toContainEqual({ kind: 'ref', ref: { type: 'task', id: 'TASK-410', label: 'TASK-410' } });
    expect(n.segments).toContainEqual({ kind: 'ref', ref: { type: 'agent', id: 'dev_agent', label: 'dev_agent' } });
    expect(n.detail).toBe('团队 engineering');
  });

  it('job sentence reorders the parent-task slot without losing either ref', () => {
    const n = describeAuditEntry(
      entry({
        action: 'job_submitted',
        agent: 'dev_agent',
        task_id: 'TASK-680',
        payload: { script_request_id: 'JOB-083', title: 'run prod build' },
      }),
      'zh-CN',
    );
    expect(narrativeText(n)).toBe('dev_agent 在 TASK-680 上提交了 JOB-083。');
    const refs = n.segments.filter((s) => s.kind === 'ref').map((s) => (s.kind === 'ref' ? s.ref.id : ''));
    expect(refs).toEqual(['TASK-680', 'JOB-083']);
    expect(n.detail).toBe('run prod build');
  });

  it('localizes the neutral subject, generic nouns and detail units', () => {
    const n = describeAuditEntry(
      entry({
        action: 'session_end',
        agent: null,
        payload: { duration_seconds: 80, token_usage: { total: 76300 } },
      }),
      'zh-CN',
    );
    expect(n.segments[0]).toEqual({ kind: 'subject', text: zh('audit.n.subject.system') });
    expect(narrativeText(n)).toBe('系统 结束了 一个任务。');
    expect(n.detail).toBe('1 分 20 秒 · 7.6万 Token');
  });

  it('reply-wake details are translated while raw payload tokens stay verbatim', () => {
    const n = describeAuditEntry(
      entry({
        action: 'thread_reply_wake_cancelled',
        agent: 'qa_engineer',
        task_id: 'THR-9',
        payload: { boundary_seq: 4, reason: 'founder_aborted', swept_count: 2 },
      }),
      'zh-CN',
    );
    expect(narrativeText(n)).toBe('qa_engineer 在 THR-9 中取消了一次回复唤醒。');
    expect(n.detail).toBe('founder_aborted · 已丢弃至 4 · 已退役 2 个回执');
  });

  it('artifact names and unknown action names stay verbatim', () => {
    const put = describeAuditEntry(
      entry({ action: 'artifact_put', agent: 'qa_engineer', payload: { name: 'qa/report.png', size_bytes: 2048 } }),
      'zh-CN',
    );
    expect(narrativeText(put)).toBe('qa_engineer 发布了 qa/report.png。');
    expect(put.detail).toBe('2.0 KB');
    const unknown = describeAuditEntry(
      entry({ action: 'some_brand_new_event', agent: 'dev_agent', task_id: 'TASK-1' }),
      'zh-CN',
    );
    expect(narrativeText(unknown)).toBe('dev_agent 在 TASK-1 上 some brand new event。');
  });
});
