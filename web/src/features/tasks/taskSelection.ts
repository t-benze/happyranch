/**
 * Pure per-task model/effort form logic (THR-356).
 *
 * Wire contract (runtime/daemon/routes/tasks.py): on submit a field is either
 * sent or absent; on revisit an OMITTED field carries the predecessor's
 * override and an explicit `null` clears that field to the agent default.
 * Values are never trimmed or rewritten here; the daemon validates them.
 */
import type { InvalidTaskSelectionDetail } from '@/lib/api/types';

/** Form text for each field; '' means "no value typed/selected". */
export interface SelectionDraft {
  model: string;
  effort: string;
}

/** The predecessor's stored override (revisit only). */
export interface CarriedSelection {
  model: string | null;
  effort: string | null;
}

export type FieldIntent = 'default' | 'set' | 'carry' | 'change' | 'clear';

export function fieldIntent(draft: string, carried: string | null | undefined): FieldIntent {
  if (carried === undefined) return draft === '' ? 'default' : 'set';
  if (draft === (carried ?? '')) return carried === null ? 'default' : 'carry';
  if (draft === '') return 'clear';
  return carried === null ? 'set' : 'change';
}

function fieldValue(draft: string, carried: string | null | undefined): string | null | undefined {
  const intent = fieldIntent(draft, carried);
  if (intent === 'default' || intent === 'carry') return undefined;
  return intent === 'clear' ? null : draft;
}

/** Only the selection keys the daemon should receive; unset adds none. */
export function selectionBody(
  draft: SelectionDraft,
  carried?: CarriedSelection,
): { model?: string | null; reasoning_effort?: string | null } {
  const body: { model?: string | null; reasoning_effort?: string | null } = {};
  const model = fieldValue(draft.model, carried ? carried.model : undefined);
  const effort = fieldValue(draft.effort, carried ? carried.effort : undefined);
  if (model !== undefined) body.model = model;
  if (effort !== undefined) body.reasoning_effort = effort;
  return body;
}

/** The daemon's 422 `invalid_task_selection` detail, or null for any other error. */
export function selectionRefusal(err: unknown): InvalidTaskSelectionDetail | null {
  const detail = (err as { detail?: unknown } | null | undefined)?.detail;
  if (!detail || typeof detail !== 'object') return null;
  const d = detail as Record<string, unknown>;
  if (d.code !== 'invalid_task_selection' || typeof d.reason !== 'string') return null;
  return {
    code: 'invalid_task_selection',
    reason: d.reason,
    executor: typeof d.executor === 'string' ? d.executor : '',
    valid_options: Array.isArray(d.valid_options)
      ? d.valid_options.filter((v): v is string => typeof v === 'string') : [],
  };
}
