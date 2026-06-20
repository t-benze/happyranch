/**
 * URL <-> AuditFilters codec.
 *
 * Filters live in the page URL so deep links, refresh, and back/forward
 * all behave the same.
 *
 * - ``action`` is a single active legend filter (one event class at a time).
 * - ``since`` is a window token (24h / 7d / all).
 * - ``agent`` / ``task_id`` are deep-link params carried forward.
 */
import type { AuditEntry } from '@/lib/api/types';

export type SinceToken = '24h' | '7d' | 'all';

export interface AuditFilters {
  agent: string | null;
  action: string | null;
  since: SinceToken | null;
  task_id: string | null;
}

const SINCE_TOKENS: ReadonlyArray<SinceToken> = ['24h', '7d', 'all'];

function isSinceToken(s: string | null): s is SinceToken {
  return s !== null && (SINCE_TOKENS as readonly string[]).includes(s);
}

export function decodeFilters(params: URLSearchParams): AuditFilters {
  const sinceRaw = params.get('since');
  return {
    agent: params.get('agent') || null,
    action: params.get('action') || null,
    since: isSinceToken(sinceRaw) ? sinceRaw : null,
    task_id: params.get('task_id') || null,
  };
}

export function encodeFilters(f: AuditFilters): string {
  const p = new URLSearchParams();
  if (f.agent) p.set('agent', f.agent);
  if (f.action) p.set('action', f.action);
  if (f.since) p.set('since', f.since);
  if (f.task_id) p.set('task_id', f.task_id);
  return p.toString();
}

const DAY_MS = 24 * 60 * 60 * 1000;

export function sinceToISO(
  token: SinceToken | null,
  now: Date = new Date(),
): string | null {
  if (token === null || token === 'all') return null;
  const days = token === '24h' ? 1 : 7;
  return new Date(now.getTime() - days * DAY_MS).toISOString();
}

// ---------------------------------------------------------------------------
// Legend — event-type counts from the current (unfiltered) entries
// ---------------------------------------------------------------------------

export interface LegendEntry {
  /** Action value (e.g. 'completion_report', 'escalation'). */
  action: string;
  /** Human-readable label for the legend chip. */
  label: string;
  /** Count of matching entries in the current window. */
  count: number;
  /** Dot color mapped to LOCKED Pasture semantic tokens
   *  (positive/attention/danger/info). */
  color: 'positive' | 'attention' | 'danger' | 'info';
}

/** Dot color map keyed by LegendEntry.color → Tailwind bg-<token> utility.
 *  Shared between AuditPage legend chips and AuditTimeline row dots. */
export const DOT_COLOR_CLASS: Record<LegendEntry['color'], string> = {
  positive: 'bg-positive',
  attention: 'bg-attention',
  danger: 'bg-danger',
  info: 'bg-feedback-info',
};

const EVENT_CLASS_MAP: Record<string, { label: string; color: LegendEntry['color'] }> = {
  completion_report: { label: 'Completed', color: 'positive' },
  session_end: { label: 'Completed', color: 'positive' },
  session_start: { label: 'Started', color: 'info' },
  review_verdict: { label: 'Reviewed', color: 'info' },
  task_cancelled: { label: 'Cancelled', color: 'attention' },
  merge_pr_opened: { label: 'Merge', color: 'attention' },
  merge_pr_merged: { label: 'Merge', color: 'attention' },
  merge_pr_closed: { label: 'Merge', color: 'attention' },
  job_submitted: { label: 'Job', color: 'attention' },
  job_rejected: { label: 'Job', color: 'attention' },
  job_run_started: { label: 'Job', color: 'attention' },
  job_auto_started: { label: 'Job', color: 'attention' },
  job_run_completed: { label: 'Job', color: 'attention' },
  job_run_failed: { label: 'Job', color: 'danger' },
  job_stopped: { label: 'Job', color: 'attention' },
  escalation: { label: 'Escalation', color: 'danger' },
  escalation_resolved: { label: 'Escalated — resolved', color: 'positive' },
  session_timeout: { label: 'Failure', color: 'danger' },
  session_failed: { label: 'Failure', color: 'danger' },
  session_cancelled: { label: 'Cancelled', color: 'attention' },
  executor_error: { label: 'Failure', color: 'danger' },
};

const LEGEND_ORDER: string[] = [
  'completion_report',
  'session_end',
  'session_start',
  'review_verdict',
  'merge_pr_opened',
  'merge_pr_merged',
  'merge_pr_closed',
  'job_submitted',
  'job_run_completed',
  'job_run_failed',
  'escalation',
  'escalation_resolved',
  'session_timeout',
  'session_failed',
  'executor_error',
];

export function buildLegend(entries: AuditEntry[]): LegendEntry[] {
  const counts = new Map<string, number>();
  for (const e of entries) {
    counts.set(e.action, (counts.get(e.action) ?? 0) + 1);
  }

  const seen = new Set<string>();
  const result: LegendEntry[] = [];

  // stable order: known actions first, then alphabetical
  for (const action of LEGEND_ORDER) {
    const count = counts.get(action);
    if (count) {
      seen.add(action);
      const cls = EVENT_CLASS_MAP[action] ?? { label: action, color: 'attention' as const };
      result.push({ action, label: cls.label, count, color: cls.color });
    }
  }
  for (const [action, count] of [...counts.entries()].sort((a, b) => a[0].localeCompare(b[0]))) {
    if (!seen.has(action)) {
      const cls = EVENT_CLASS_MAP[action] ?? { label: action, color: 'attention' as const };
      result.push({ action, label: cls.label, count, color: cls.color });
    }
  }

  return result;
}

export const FAILURE_ACTIONS = new Set([
  'escalation',
  'session_timeout',
  'session_failed',
  'executor_error',
  'job_run_failed',
  'task_cancelled',
]);

/** True when there are zero failure/escalation entries in the current view. */
export function isAllClear(entries: AuditEntry[]): boolean {
  return entries.length > 0 && entries.every((e) => !FAILURE_ACTIONS.has(e.action));
}
