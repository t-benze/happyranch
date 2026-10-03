/**
 * Jobs daemon error codes -> typed catalog keys (THR-118 W3b-2).
 *
 * Same diagnostic boundary as the Tasks `classifyTaskError` (W3b-1 F1). Dialogs
 * and the Stop action hold a locale-neutral `JobErrorView` in state and render
 * it through `t` on every render, so a mapped message re-translates in place on
 * a locale switch while daemon diagnostics stay byte-for-byte verbatim.
 */
import type { MessageKey } from '@/lib/i18n';

const JOBS_ERROR_KEYS: Record<string, MessageKey> = {
  unknown_job: 'jobs.error.unknownJob',
  not_pending: 'jobs.error.notPending',
  not_running: 'jobs.error.notRunning',
  invalid_timeout: 'jobs.error.invalidTimeout',
  empty_reason: 'jobs.reject.required',
  reason_too_long: 'jobs.reject.tooLong',
};

export type JobErrorView = { kind: 'message'; key: MessageKey } | { kind: 'raw'; text: string };

/**
 * Classify a thrown value as a descriptor instead of an English string:
 *  - a recognized product code -> its catalog key;
 *  - any other non-empty code -> the raw code, verbatim;
 *  - no/empty code but a non-empty string diagnostic -> that text, verbatim
 *    (an ApiError-shaped value's string `detail`, or a plain Error's message /
 *    a thrown string; ApiError's synthetic 'API <status>' message is never one);
 *  - otherwise -> the localized `fallback`, so an error is never blank.
 */
export function classifyJobError(err: unknown, fallback: MessageKey): JobErrorView {
  const code = (err as { code?: string } | null | undefined)?.code;
  if (code) {
    if (Object.prototype.hasOwnProperty.call(JOBS_ERROR_KEYS, code)) {
      return { kind: 'message', key: JOBS_ERROR_KEYS[code] };
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

export function renderJobError(view: JobErrorView, t: (key: MessageKey) => string): string {
  return view.kind === 'raw' ? view.text : t(view.key);
}
