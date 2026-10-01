/**
 * Tasks daemon error codes -> typed catalog keys (THR-118 W3b-1).
 *
 * Dialogs hold a locale-neutral `TaskErrorView` in state and render it through
 * `t` on every render, so a mapped message re-translates in place on a locale
 * switch while an unmapped daemon code stays byte-for-byte verbatim.
 */
import type { MessageKey } from '@/lib/i18n';

const TASKS_ERROR_KEYS: Record<string, MessageKey> = {
  task_not_escalated: 'tasks.error.notEscalated',
  cannot_revisit: 'tasks.error.cannotRevisit',
  invalid_decision: 'tasks.error.invalidDecision',
  not_found: 'tasks.error.notFound',
};

export type TaskErrorView = { kind: 'message'; key: MessageKey } | { kind: 'raw'; text: string };

/**
 * Classify a thrown value exactly like the legacy
 * `code ? (TASKS_ERROR_STRINGS[code] ?? code) : fallback` expression, as a
 * descriptor instead of an English string.
 */
export function classifyTaskError(err: unknown, fallback: MessageKey): TaskErrorView {
  const code = (err as { code?: string } | null | undefined)?.code;
  if (!code) return { kind: 'message', key: fallback };
  if (Object.prototype.hasOwnProperty.call(TASKS_ERROR_KEYS, code)) {
    return { kind: 'message', key: TASKS_ERROR_KEYS[code] };
  }
  return { kind: 'raw', text: code };
}

export function renderTaskError(view: TaskErrorView, t: (key: MessageKey) => string): string {
  return view.kind === 'raw' ? view.text : t(view.key);
}
