/**
 * Audit copy helpers (THR-118 W4b).
 *
 * All Audit product copy lives in the typed catalog under `audit.*`. The
 * timeline load error uses the same diagnostic boundary as the Jobs
 * `classifyJobError` (W3b-2) / Dreams `classifyDreamError` (W4a-1): the error
 * is classified into a locale-neutral `AuditErrorView` and rendered through
 * `t` on every render, so mapped copy re-translates in place on a locale
 * switch while daemon diagnostics stay byte-for-byte verbatim.
 */
import type { MessageKey, MessageParams } from '@/lib/i18n';

const AUDIT_ERROR_KEYS: Record<string, MessageKey> = {
  // Emitted by the per-org route dependency that guards GET /orgs/{slug}/audit.
  unknown_org: 'audit.error.unknownOrg',
};

export type AuditErrorView = { kind: 'message'; key: MessageKey } | { kind: 'raw'; text: string };

/**
 * Classify a thrown value as a descriptor instead of an English string:
 *  - a recognized product code -> its catalog key;
 *  - any other non-empty code -> the raw code, verbatim;
 *  - no/empty code but a non-empty string diagnostic -> that text, verbatim
 *    (an ApiError-shaped value's string `detail`, or a plain Error's message /
 *    a thrown string; ApiError's synthetic 'API <status>' message is never one);
 *  - otherwise -> the localized `fallback`, so an error is never blank.
 */
export function classifyAuditError(err: unknown, fallback: MessageKey): AuditErrorView {
  const code = (err as { code?: string } | null | undefined)?.code;
  if (code) {
    if (Object.prototype.hasOwnProperty.call(AUDIT_ERROR_KEYS, code)) {
      return { kind: 'message', key: AUDIT_ERROR_KEYS[code] };
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

/**
 * Render the timeline load error: the localized headline, followed by the
 * classified diagnostic when there is one. The fallback (`audit.error.load`)
 * is the headline alone, so a detail-less failure is never duplicated.
 */
export function renderAuditLoadError(
  view: AuditErrorView,
  t: (key: MessageKey, params?: MessageParams) => string,
): string {
  if (view.kind === 'message' && view.key === 'audit.error.load') return t('audit.error.load');
  const detail = view.kind === 'raw' ? view.text : t(view.key);
  return t('audit.error.loadDetail', { detail });
}
