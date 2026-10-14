/** Pure read validation/presentation. Never infer work from delegated alone. */
import type { TaskRecord } from '@/lib/api/types';
import type { MessageKey, MessageParams } from '@/lib/i18n';

export type ApplicableChildWaitState = 'waiting_children' | 'pending_children'
  | 'child_results_received' | 'continuation_pending';
export type TaskWaitingLabels = {
  child: Record<ApplicableChildWaitState, string>;
  blocked_on_job: string;
};
const CHILD_STATES = new Set(['waiting_children', 'pending_children', 'child_results_received', 'continuation_pending', 'unavailable']);

export function childWaitState(task: TaskRecord): ApplicableChildWaitState | 'unavailable' | null {
  if (task.status !== 'in_progress' || task.block_kind !== 'delegated') return null;
  const raw = task.child_wait;
  if (!raw || typeof raw !== 'object' || Array.isArray(raw)) return null;
  const keys = Object.keys(raw);
  if (keys.length !== 2 || !keys.includes('state') || !keys.includes('observed_at')
    || !CHILD_STATES.has(raw.state) || typeof raw.observed_at !== 'string') return null;
  const match = raw.observed_at.match(/^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2}):(\d{2})(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})$/);
  if (!match || !Number.isFinite(Date.parse(raw.observed_at))) return null;
  const date = new Date(raw.observed_at.slice(0, 10) + 'T00:00:00Z');
  if (!Number.isFinite(date.getTime()) || date.toISOString().slice(0, 10) !== raw.observed_at.slice(0, 10)
    || Number(match[4]) > 23 || Number(match[5]) > 59 || Number(match[6]) > 59) return null;
  return raw.state;
}

export function resolveTaskWaitingQualifier(task: TaskRecord, labels: TaskWaitingLabels): string | null {
  if (task.status === 'in_progress' && task.block_kind === 'blocked_on_job') return labels.blocked_on_job;
  const state = childWaitState(task);
  return state && state !== 'unavailable' ? labels.child[state] : null;
}

/** Feature translators resolve the complete map before pure patterns receive it. */
export function taskWaitingLabels(t: (key: MessageKey, params?: MessageParams) => string): TaskWaitingLabels {
  return {child: {
    waiting_children: t('tasks.waiting.children'),
    pending_children: t('tasks.waiting.pendingChildren'),
    child_results_received: t('tasks.waiting.resultsReceived'),
    continuation_pending: t('tasks.waiting.continuationPending'),
  }, blocked_on_job: t('tasks.waiting.jobs')};
}
