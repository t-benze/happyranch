/**
 * Dreams copy helpers (THR-118 W4a-1).
 *
 * All Dreams product copy lives in the typed catalog under `dreams.*`. This
 * file maps daemon status tokens and error codes onto catalog keys. Unknown
 * status tokens render verbatim (daemon values are never translated).
 *
 * Accept/Dismiss errors use the same diagnostic boundary as the Jobs
 * `classifyJobError` (W3b-2): the drawer holds a locale-neutral
 * `DreamErrorView` in state and renders it through `t` on every render, so a
 * mapped message re-translates in place on a locale switch while daemon
 * diagnostics stay byte-for-byte verbatim.
 */
import type { MessageKey } from '@/lib/i18n';

const STATUS_KEYS: Record<string, MessageKey> = {
  completed: 'dreams.status.completed',
  failed: 'dreams.status.failed',
  missed: 'dreams.status.missed',
  running: 'dreams.status.running',
  timeout: 'dreams.status.timeout',
  skipped: 'dreams.status.skipped',
  pending: 'dreams.status.pending',
};

/** Localized label for a known dream status; an unknown token stays verbatim. */
export function dreamStatusLabel(status: string, t: (key: MessageKey) => string): string {
  return Object.prototype.hasOwnProperty.call(STATUS_KEYS, status) ? t(STATUS_KEYS[status]) : status;
}

const DREAMS_ERROR_KEYS: Record<string, MessageKey> = {
  candidate_not_found: 'dreams.error.candidateNotFound',
  candidate_already_decided: 'dreams.error.candidateAlreadyDecided',
  candidate_already_promoted: 'dreams.error.candidateAlreadyPromoted',
};

export type DreamErrorView = { kind: 'message'; key: MessageKey } | { kind: 'raw'; text: string };

/**
 * Classify a thrown value as a descriptor instead of an English string:
 *  - a recognized product code -> its catalog key;
 *  - any other non-empty code -> the raw code, verbatim;
 *  - no/empty code but a non-empty string diagnostic -> that text, verbatim
 *    (an ApiError-shaped value's string `detail`, or a plain Error's message /
 *    a thrown string; ApiError's synthetic 'API <status>' message is never one);
 *  - otherwise -> the localized `fallback`, so an error is never blank.
 */
export function classifyDreamError(err: unknown, fallback: MessageKey): DreamErrorView {
  const code = (err as { code?: string } | null | undefined)?.code;
  if (code) {
    if (Object.prototype.hasOwnProperty.call(DREAMS_ERROR_KEYS, code)) {
      return { kind: 'message', key: DREAMS_ERROR_KEYS[code] };
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

export function renderDreamError(view: DreamErrorView, t: (key: MessageKey) => string): string {
  return view.kind === 'raw' ? view.text : t(view.key);
}
