/** Pure finished-jobs projection shared by the task detail page and task cards. */
import type { JobRecord, TaskRecord } from '@/lib/api/types';
import type { MessageKey, MessageParams } from '@/lib/i18n';

export type BlockedJobEntry = { job_id: string; status: string };

const TERMINAL_JOB_STATUSES = new Set(['completed', 'failed', 'rejected']);

/**
 * A job-parked task whose blocking jobs are ALL terminal is no longer waiting on
 * jobs: it is waiting to be resumed. When an effective root Pause holds it, say
 * so; otherwise say the resume is pending. Null keeps the ordinary projection.
 */
export function finishedJobsQualifier(
  task: TaskRecord,
  blockedOnJobs: BlockedJobEntry[] | null | undefined,
  t: (key: MessageKey, params?: MessageParams) => string,
): string | null {
  if (task.status !== 'in_progress' || task.block_kind !== 'blocked_on_job') return null;
  if (!blockedOnJobs || blockedOnJobs.length === 0
    || !blockedOnJobs.every(entry => TERMINAL_JOB_STATUSES.has(entry.status))) return null;
  return t(task.pause?.effective_hold === true ? 'tasks.waiting.jobsFinishedHeld' : 'tasks.waiting.jobsFinishedResumePending');
}

/** List items carry blocking IDs as a JSON string; anything but a non-empty string array is unknown. */
export function blockedOnJobIds(task: TaskRecord): string[] | null {
  const raw = task.blocked_on_job_ids;
  if (typeof raw !== 'string') return null;
  let parsed: unknown;
  try { parsed = JSON.parse(raw); } catch { return null; }
  if (!Array.isArray(parsed) || parsed.length === 0
    || !parsed.every((id): id is string => typeof id === 'string' && id.length > 0)) return null;
  return parsed;
}

/** Statuses for every blocking ID from a job listing; any missing ID is unknown (null). */
export function blockedJobsFromList(ids: string[] | null, jobs: JobRecord[] | null | undefined): BlockedJobEntry[] | null {
  if (!ids || !Array.isArray(jobs)) return null;
  const byId = new Map(jobs.map(job => [job.id, job.status] as const));
  const entries: BlockedJobEntry[] = [];
  for (const id of ids) {
    const status = byId.get(id);
    if (typeof status !== 'string') return null;
    entries.push({ job_id: id, status });
  }
  return entries;
}
