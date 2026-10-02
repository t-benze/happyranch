/**
 * Work Hours presentation helpers (THR-118 W4b).
 *
 * `classifyWorkHoursError` is the same F1 diagnostic boundary as
 * `classifyJobError` / `classifyDreamError`, used for the load-error sites
 * (settings recovery banner, wakes list). Save 422s keep going through
 * `extractServerErrors` and render verbatim. No Work Hours read route returns a
 * product code the UI owns copy for, so the recognized-code map is empty:
 * every code renders raw.
 *
 * `cadenceSummaryFor` is the localized twin of `merge.ts`'s `cadenceSummary`:
 * same composition and separators, with only the English words routed through
 * the catalog; interval/window/days/timezone config values stay verbatim.
 */
import type { MessageKey, MessageParams } from '@/lib/i18n';
import type { EffectiveSchedule } from './merge';

const WORK_HOURS_ERROR_KEYS: Record<string, MessageKey> = {};

export type WorkHoursErrorView = { kind: 'message'; key: MessageKey } | { kind: 'raw'; text: string };

/**
 *  - a recognized product code -> its catalog key;
 *  - any other non-empty code -> the raw code, verbatim;
 *  - no/empty code but a non-empty string diagnostic -> that text, verbatim
 *    (an ApiError-shaped value's string `detail`, or a plain Error's message /
 *    a thrown string; ApiError's synthetic 'API <status>' message is never one);
 *  - otherwise -> the localized `fallback`, so an error is never blank.
 */
export function classifyWorkHoursError(err: unknown, fallback: MessageKey): WorkHoursErrorView {
  const code = (err as { code?: string } | null | undefined)?.code;
  if (code) {
    if (Object.prototype.hasOwnProperty.call(WORK_HOURS_ERROR_KEYS, code)) {
      return { kind: 'message', key: WORK_HOURS_ERROR_KEYS[code] };
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

export function renderWorkHoursError(
  view: WorkHoursErrorView,
  t: (key: MessageKey) => string,
): string {
  return view.kind === 'raw' ? view.text : t(view.key);
}

export function cadenceSummaryFor(
  eff: EffectiveSchedule,
  t: (key: MessageKey, params?: MessageParams) => string,
): string {
  if (!eff.mode) return t('workHours.cadence.inherits');
  const every = t('workHours.cadence.every', { interval: eff.interval ?? '—' });
  if (eff.mode === 'continuous') {
    return `${every} (24/7)`;
  }
  // windowed
  const window =
    eff.start && eff.end ? `${eff.start}–${eff.end}` : t('workHours.cadence.windowUnset');
  const days =
    eff.days && eff.days.length > 0 ? eff.days.join(',') : t('workHours.cadence.daysUnset');
  const tz = eff.timezone ?? '';
  return `${every} · ${window} ${days}${tz ? ` ${tz}` : ''}`.trim();
}

/** Known wake statuses -> catalog keys; unknown tokens render verbatim. */
export const WAKE_STATUS_KEYS: Record<string, MessageKey> = {
  pending: 'workHours.wakes.status.pending',
  running: 'workHours.wakes.status.running',
  completed: 'workHours.wakes.status.completed',
  failed: 'workHours.wakes.status.failed',
  timeout: 'workHours.wakes.status.timeout',
  skipped: 'workHours.wakes.status.skipped',
};
