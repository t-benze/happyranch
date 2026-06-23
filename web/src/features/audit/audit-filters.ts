/**
 * URL <-> AuditFilters codec.
 *
 * Filters live in the page URL so deep links, refresh, and back/forward
 * all behave the same.
 *
 * - ``eventClass`` is the active right-rail legend filter — one of the five
 *   human event classes (dispatch / completed / merge / escalation / failure).
 *   It narrows the timeline CLIENT-SIDE off already-fetched rows (AUDIT-02).
 * - ``action`` is a raw event-type deep-link param honoured by the /audit API.
 * - ``since`` is a window token (24h / 7d / all).
 * - ``agent`` / ``task_id`` are deep-link params carried forward.
 */
import type { AuditEntry } from '@/lib/api/types';

export type SinceToken = '24h' | '7d' | 'all';

export interface AuditFilters {
  agent: string | null;
  action: string | null;
  eventClass: EventClass | null;
  since: SinceToken | null;
  task_id: string | null;
}

const SINCE_TOKENS: ReadonlyArray<SinceToken> = ['24h', '7d', 'all'];

function isSinceToken(s: string | null): s is SinceToken {
  return s !== null && (SINCE_TOKENS as readonly string[]).includes(s);
}

export function decodeFilters(params: URLSearchParams): AuditFilters {
  const sinceRaw = params.get('since');
  const classRaw = params.get('class');
  return {
    agent: params.get('agent') || null,
    action: params.get('action') || null,
    eventClass: isEventClass(classRaw) ? classRaw : null,
    since: isSinceToken(sinceRaw) ? sinceRaw : null,
    task_id: params.get('task_id') || null,
  };
}

export function encodeFilters(f: AuditFilters): string {
  const p = new URLSearchParams();
  if (f.agent) p.set('agent', f.agent);
  if (f.action) p.set('action', f.action);
  if (f.eventClass) p.set('class', f.eventClass);
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
// Event classes — the five human buckets the design's right-rail legend shows
// (AUDIT-02). The ~57 raw audit `action` values (the single writer is
// runtime/infrastructure/audit_logger.py, mirrored client-side by the AUDIT-01
// narrative switch in audit-narrative.ts) collapse into exactly these five.
// This is the SINGLE source of truth for the raw-event-type → class mapping;
// counts and the timeline filter both derive from `classOf`.
// ---------------------------------------------------------------------------

export type EventClass = 'dispatch' | 'completed' | 'merge' | 'escalation' | 'failure';

/** Fixed display order, matching the design's right-rail panel. */
export const EVENT_CLASS_ORDER: readonly EventClass[] = [
  'dispatch',
  'completed',
  'merge',
  'escalation',
  'failure',
] as const;

export type DotColor = 'neutral' | 'positive' | 'info' | 'attention' | 'danger';

/** Dot color map keyed by DotColor → Tailwind bg-<token> utility. Shared
 *  between the right-rail legend dots and the AuditTimeline per-row dots. */
export const DOT_COLOR_CLASS: Record<DotColor, string> = {
  neutral: 'bg-fg-muted',
  positive: 'bg-positive',
  info: 'bg-feedback-info',
  attention: 'bg-attention',
  danger: 'bg-danger',
};

/** Per-class label + dot color (locked Pasture semantic tokens). */
export const EVENT_CLASS_META: Record<EventClass, { label: string; color: DotColor }> = {
  dispatch: { label: 'Dispatch', color: 'neutral' },
  completed: { label: 'Completed', color: 'positive' },
  merge: { label: 'Merge', color: 'info' },
  escalation: { label: 'Escalation', color: 'attention' },
  failure: { label: 'Failure', color: 'danger' },
};

function isEventClass(s: string | null): s is EventClass {
  return s !== null && (EVENT_CLASS_ORDER as readonly string[]).includes(s);
}

/**
 * Raw event-type → human class. Enumerated to cover the full audit-narrative.ts
 * / audit_logger.py action vocabulary; any value not listed (a future/unknown
 * action) falls back to `dispatch` via `classOf` so NO event is ever dropped.
 *
 * Ambiguous calls are documented in the AUDIT-02 completion notes:
 *  - review_verdict → completed (a finished review producing a verdict)
 *  - escalation_resolved → completed (a resolved escalation is a positive end)
 *  - job_rejected, task_cancelled, *_cancelled → failure (non-success terminal)
 *  - *_skipped, job_stopped → dispatch (neutral lifecycle routing)
 *  - merge_pr_* → merge (NOT currently emitted by the daemon; count shows 0)
 */
const ACTION_CLASS: Record<string, EventClass> = {
  // --- completed: terminal success of a work unit -------------------------
  completion_report: 'completed',
  session_end: 'completed',
  review_verdict: 'completed',
  escalation_resolved: 'completed',
  job_run_completed: 'completed',
  dream_completed: 'completed',
  work_hour_completed: 'completed',

  // --- merge: PR / code integration (not currently emitted) ---------------
  merge_pr_opened: 'merge',
  merge_pr_merged: 'merge',
  merge_pr_closed: 'merge',

  // --- escalation: routed up to the founder for a decision ----------------
  escalation: 'escalation',
  escalation_superseded: 'escalation',

  // --- failure: error / timeout / non-success terminal state --------------
  session_timeout: 'failure',
  session_failed: 'failure',
  session_cancelled: 'failure',
  executor_error: 'failure',
  daemon_restart_failure: 'failure',
  job_run_failed: 'failure',
  job_rejected: 'failure',
  dream_failed: 'failure',
  dream_timeout: 'failure',
  work_hour_failed: 'failure',
  work_hour_timeout: 'failure',
  thread_invocation_failed: 'failure',
  task_cancelled: 'failure',

  // --- dispatch: work initiated / routed / advanced / side-effects --------
  session_start: 'dispatch',
  orchestration_step: 'dispatch',
  chain_auto_advance: 'dispatch',
  revisit_of: 'dispatch',
  auto_revisit_of: 'dispatch',
  revisit_spawned: 'dispatch',
  progress: 'dispatch',
  task_blocked_on_jobs: 'dispatch',
  task_resumed_from_jobs: 'dispatch',
  task_resume_skipped: 'dispatch',
  thread_started: 'dispatch',
  thread_message_sent: 'dispatch',
  thread_decline_consumed: 'dispatch',
  thread_participant_added: 'dispatch',
  thread_dispatch: 'dispatch',
  agent_session_reused: 'dispatch',
  agent_session_evicted_fallback: 'dispatch',
  thread_task_followup_enqueued: 'dispatch',
  thread_followup_skipped: 'dispatch',
  thread_turn_cap_auto_extended: 'dispatch',
  thread_archived: 'dispatch',
  thread_resumed: 'dispatch',
  agent_managed: 'dispatch',
  agent_backfilled: 'dispatch',
  artifact_put: 'dispatch',
  artifact_delete: 'dispatch',
  learning_added: 'dispatch',
  learning_updated: 'dispatch',
  learning_promoted: 'dispatch',
  dream_scheduled: 'dispatch',
  dream_started: 'dispatch',
  dream_founder_thread_created: 'dispatch',
  work_hour_scheduled: 'dispatch',
  work_hour_started: 'dispatch',
  work_hour_spawned: 'dispatch',
  job_submitted: 'dispatch',
  job_run_started: 'dispatch',
  job_auto_started: 'dispatch',
  job_stopped: 'dispatch',
};

/** Collapse a raw event-type into one of the five classes. Unknown/future
 *  actions fall back to `dispatch` so every event maps to exactly one class. */
export function classOf(action: string): EventClass {
  return ACTION_CLASS[action] ?? 'dispatch';
}

export interface ClassLegendEntry {
  eventClass: EventClass;
  /** Human-readable label for the legend row. */
  label: string;
  /** Count of entries in the current window that belong to this class. */
  count: number;
  /** Dot color token (→ DOT_COLOR_CLASS). */
  color: DotColor;
}

/**
 * Build the fixed five-row class legend from the already-fetched entries.
 * Always returns all five classes in `EVENT_CLASS_ORDER` (a stable taxonomy,
 * zero-count classes included) so the right rail never reflows. The per-class
 * counts partition the input: every entry contributes to exactly one class, so
 * the counts sum to `entries.length`.
 */
export function buildClassLegend(entries: AuditEntry[]): ClassLegendEntry[] {
  const counts = new Map<EventClass, number>();
  for (const c of EVENT_CLASS_ORDER) counts.set(c, 0);
  for (const e of entries) {
    const c = classOf(e.action);
    counts.set(c, (counts.get(c) ?? 0) + 1);
  }
  return EVENT_CLASS_ORDER.map((c) => ({
    eventClass: c,
    label: EVENT_CLASS_META[c].label,
    count: counts.get(c) ?? 0,
    color: EVENT_CLASS_META[c].color,
  }));
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
