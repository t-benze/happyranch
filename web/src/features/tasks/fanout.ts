/**
 * Fan-out display derivations for the Task detail surface (TASK-1717 polish).
 *
 * Presentation-only. Every value here is DERIVED from data the task-detail
 * and recall contracts already return — no new backend fields are assumed:
 *
 *  - `active_fanout` (JSON string on the TaskRecord) → spawned band
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
import { translate, type Locale } from '@/lib/i18n';

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
  width: number | null;
  /** Planned children for a pending fan-out (children_details). Empty when
   *  the payload does not carry structured planned-child metadata. */
  plannedChildren: FanoutPlannedChild[];
  /** Exact selected IDs; null preserves missing/malformed membership. */
  childrenIds: string[] | null;
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

function parseChildIds(raw: unknown): string[] | null {
  if (!Array.isArray(raw) || raw.length === 0
    || raw.some(v => typeof v !== 'string' || !v.trim())
    || new Set(raw).size !== raw.length) return null;
  return raw;
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
  const width = typeof rec.width === 'number' ? rec.width : null;
  if (status !== 'spawned') {
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
  childrenIds: string[] | null;
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

/** Seven disjoint current row-lifecycle categories, never execution/success proof. */
export interface ChildStatusCounts {
  total: number;
  pending: number;
  inProgress: number;
  escalated: number;
  completed: number;
  failed: number;
  cancelled: number;
  superseded: number;
  terminal: number;
}
const CATEGORIES = ['pending', 'inProgress', 'escalated', 'completed', 'failed', 'cancelled', 'superseded'] as const;
const CATEGORY: Partial<Record<TaskStatus, typeof CATEGORIES[number]>> = {
  pending: 'pending', in_progress: 'inProgress', escalated: 'escalated',
  completed: 'completed', failed: 'failed', cancelled: 'cancelled', superseded: 'superseded',
};

/** Only complete exact selected membership admits numbers. No all-child fallback. */
export function summarizeChildStatuses(
  children: TaskRecallNode[] | undefined,
  selectedIds: string[] | null | undefined,
  width: number | null | undefined,
): ChildStatusCounts | null {
  const ids = parseChildIds(selectedIds);
  if (!Array.isArray(children) || !ids || !Number.isSafeInteger(width) || !width
    || width < 1 || width !== ids.length) return null;
  const counts: ChildStatusCounts = {
    total: ids.length, pending: 0, inProgress: 0, escalated: 0, completed: 0,
    failed: 0, cancelled: 0, superseded: 0, terminal: 0,
  };
  for (const id of ids) {
    const matches = children.filter(child => child && child.task_id === id);
    if (matches.length !== 1) return null;
    const child = matches[0];
    if (!Array.isArray(child.children) || typeof child.brief !== 'string'
      || !Object.hasOwn(CATEGORY, child.status)) return null;
    const category = CATEGORY[child.status]!;
    counts[category] += 1;
  }
  counts.terminal = counts.completed + counts.failed + counts.cancelled + counts.superseded;
  return counts;
}

/** All segments/summary use the same literal current row categories. */
export function progressSummary(counts: ChildStatusCounts, locale: Locale): string {
  const parts = [translate(locale, 'tasks.fanout.progress.terminal', {
    terminal: counts.terminal, total: counts.total,
  })];
  for (const category of CATEGORIES) {
    if (counts[category] > 0) parts.push(translate(locale, `tasks.fanout.progress.${category}`, {count: counts[category]}));
  }
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
