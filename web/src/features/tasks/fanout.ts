/**
 * Fan-out display derivations for the Task detail surface (TASK-1717 polish).
 *
 * Presentation-only. Every value here is DERIVED from data the task-detail
 * and recall contracts already return — no new backend fields are assumed:
 *
 *  - `active_fanout` (JSON string on the TaskRecord) → pending/running band
 *    context: status, width, planned children (children_details), spawned
 *    child ids (children_ids).
 *  - the latest `fanout_join` audit row (on the task-detail envelope) →
 *    joined band context, because run_step clears `active_fanout` after the
 *    join is injected.
 *  - recall child statuses → progress/terminal counts.
 *
 * Honesty rules from the TASK-1696 Step 0 reconciliation are enforced here:
 * we only surface agent + prompt snippets that are literally present in the
 * payload. We never fabricate locale labels, token counts, executor/model
 * values, artifact links, or merge summaries.
 */
import type { TaskRecallNode, TaskStatus } from '@/lib/api/types';

/** A planned child in a pending fan-out, parsed from `children_details`.
 *  Both fields are optional because the payload may only carry one of them —
 *  we degrade honestly rather than invent the missing half. */
export interface FanoutPlannedChild {
  agent: string | null;
  prompt: string | null;
}

/** Sanitized `active_fanout` display context. Only `spawned` is accepted
 *  (pending_review removed per THR-012 msg 129/131). Any other/absent/
 *  malformed value returns null so the caller falls back to ordinary
 *  block_kind display. */
export interface ActiveFanout {
  status: 'spawned';
  width: number;
  /** Planned children for a pending fan-out (children_details). Empty when
   *  the payload does not carry structured planned-child metadata. */
  plannedChildren: FanoutPlannedChild[];
  /** Spawned child task ids for a running fan-out (children_ids). Empty when
   *  absent — the caller then falls back to all direct recall children. */
  childrenIds: string[];
}

function coerceString(v: unknown): string | null {
  return typeof v === 'string' && v.trim() ? v : null;
}

function parsePlannedChildren(raw: unknown): FanoutPlannedChild[] {
  if (!Array.isArray(raw)) return [];
  const out: FanoutPlannedChild[] = [];
  for (const entry of raw) {
    if (entry && typeof entry === 'object') {
      const rec = entry as Record<string, unknown>;
      const agent = coerceString(rec.agent);
      const prompt = coerceString(rec.prompt) ?? coerceString(rec.brief);
      if (agent || prompt) out.push({ agent, prompt });
    }
  }
  return out;
}

function parseChildIds(raw: unknown): string[] {
  if (!Array.isArray(raw)) return [];
  return raw.filter((v): v is string => typeof v === 'string' && !!v);
}

/** Parse a task's `active_fanout` JSON string into a sanitized display
 *  context. Returns null when absent, unparseable, or missing required keys. */
export function parseActiveFanout(raw: unknown): ActiveFanout | null {
  if (typeof raw !== 'string') return null;
  let parsed: unknown;
  try {
    parsed = JSON.parse(raw);
  } catch {
    return null;
  }
  if (!parsed || typeof parsed !== 'object') return null;
  const rec = parsed as Record<string, unknown>;
  const status = typeof rec.status === 'string' ? rec.status : '';
  const width = typeof rec.width === 'number' ? rec.width : 0;
  if (status !== 'spawned' || width <= 0) {
    return null;
  }
  return {
    status,
    width,
    plannedChildren: parsePlannedChildren(rec.children_details),
    childrenIds: parseChildIds(rec.children_ids),
  };
}

/** Joined fan-out context, derived from the latest `fanout_join` audit row. */
export interface JoinedFanout {
  /** Planned width from the join payload, or null when not recorded. */
  width: number | null;
  /** Child task ids recorded on the join payload (may be empty). */
  childrenIds: string[];
}

/** Pull the most recent `fanout_join` audit row and extract width/children_ids.
 *  Returns null when no join row exists — the caller then shows no joined
 *  band. Defensive against arbitrary audit-log shapes. */
export function latestFanoutJoin(auditLog: unknown[] | undefined): JoinedFanout | null {
  if (!Array.isArray(auditLog)) return null;
  for (let i = auditLog.length - 1; i >= 0; i--) {
    const entry = auditLog[i] as { action?: unknown; payload?: unknown } | null;
    if (entry && entry.action === 'fanout_join') {
      const payload =
        entry.payload && typeof entry.payload === 'object'
          ? (entry.payload as Record<string, unknown>)
          : {};
      const width = typeof payload.width === 'number' ? payload.width : null;
      return { width, childrenIds: parseChildIds(payload.children_ids) };
    }
  }
  return null;
}

/** Terminal/progress counts across a set of recall children. */
export interface ChildStatusCounts {
  total: number;
  completed: number;
  failed: number;
  /** in_progress (running) children. */
  running: number;
  /** pending + any non-terminal, non-running status. */
  queued: number;
  /** completed + failed + cancelled + superseded. */
  terminal: number;
}

const TERMINAL: ReadonlySet<TaskStatus> = new Set<TaskStatus>([
  'completed',
  'failed',
  'cancelled',
  'superseded',
]);

/** Summarize the direct children's statuses. When `restrictIds` is provided
 *  and non-empty, only children whose task_id is in that set are counted
 *  (matching a fan-out's own `children_ids`); otherwise all direct children
 *  are counted. Nested grandchildren (e.g. auto-created follow-ups) are not
 *  counted toward fan-out width — only the top-level fan-out siblings are. */
export function summarizeChildStatuses(
  children: TaskRecallNode[] | undefined,
  restrictIds?: string[],
): ChildStatusCounts {
  const counts: ChildStatusCounts = {
    total: 0,
    completed: 0,
    failed: 0,
    running: 0,
    queued: 0,
    terminal: 0,
  };
  if (!Array.isArray(children)) return counts;
  const restrict =
    restrictIds && restrictIds.length > 0 ? new Set(restrictIds) : null;
  for (const c of children) {
    if (restrict && !restrict.has(c.task_id)) continue;
    counts.total += 1;
    switch (c.status) {
      case 'completed':
      case 'superseded':
        counts.completed += 1;
        break;
      case 'failed':
        counts.failed += 1;
        break;
      case 'cancelled':
        // terminal but not a success or an error worth its own segment
        break;
      case 'in_progress':
        counts.running += 1;
        break;
      default:
        counts.queued += 1;
        break;
    }
    if (TERMINAL.has(c.status)) counts.terminal += 1;
  }
  return counts;
}

/** Compact "N of M complete · a running · b failed · c queued" progress line.
 *  Omits any zero segment past the leading complete count. */
export function progressSummary(counts: ChildStatusCounts): string {
  const parts = [`${counts.completed} of ${counts.total} complete`];
  if (counts.running > 0) parts.push(`${counts.running} running`);
  if (counts.failed > 0) parts.push(`${counts.failed} failed`);
  if (counts.queued > 0) parts.push(`${counts.queued} queued`);
  return parts.join(' · ');
}

/** First-line, length-capped snippet of a free-text field for a compact row.
 *  Returns null for empty input so callers can omit the element entirely. */
export function snippet(text: string | null | undefined, max = 96): string | null {
  if (!text) return null;
  const firstLine = text.replace(/\s+/g, ' ').trim();
  if (!firstLine) return null;
  return firstLine.length > max
    ? firstLine.slice(0, max).replace(/\s+\S*$/, '') + '…'
    : firstLine;
}
