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
 * Classify a thrown value as a descriptor instead of an English string:
 *  - a recognized product code -> its catalog key;
 *  - any other non-empty code -> the raw code, verbatim;
 *  - no/empty code but a non-empty string diagnostic -> that text, verbatim
 *    (an ApiError-shaped value's string `detail`, or a plain Error's message /
 *    a thrown string; ApiError's synthetic 'API <status>' message is never one);
 *  - otherwise -> the localized `fallback`, so an error is never blank.
 */
export function classifyTaskError(err: unknown, fallback: MessageKey): TaskErrorView {
  const code = (err as { code?: string } | null | undefined)?.code;
  if (code) {
    if (Object.prototype.hasOwnProperty.call(TASKS_ERROR_KEYS, code)) {
      return { kind: 'message', key: TASKS_ERROR_KEYS[code] };
    }
    return { kind: 'raw', text: code };
  }
  const diagnostic = rawDiagnostic(err);
  return diagnostic === null ? { kind: 'message', key: fallback } : { kind: 'raw', text: diagnostic };
}

function rawDiagnostic(err: unknown): string | null {
  let text: unknown = null;
  if (typeof err === 'string') text = err;
  else if (err !== null && typeof err === 'object' && 'detail' in err) text = err.detail;
  else if (err instanceof Error) text = err.message;
  return typeof text === 'string' && text.trim() !== '' ? text : null;
}

export function renderTaskError(view: TaskErrorView, t: (key: MessageKey) => string): string {
  return view.kind === 'raw' ? view.text : t(view.key);
}
