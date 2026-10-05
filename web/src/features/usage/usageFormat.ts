/**
 * Display helpers for Usage v1 (THR-272 PR4).
 *
 * Every value here is formatting of a figure the daemon already computed —
 * windows, medians, rates, coverage and deltas all come from
 * `GET /usage/workload` and `GET /usage/efficiency`. Nothing in this module
 * derives a window, a percentage change or a baseline client-side.
 */
import { formatCountFor, formatTokensFor, formatDateShapeFor, translate, type Locale } from '@/lib/i18n';

/**
 * "Sep 22, 14:03" from a daemon `*_local` ISO string. Reads the wall-clock
 * parts written in the string (already rendered in the org timezone), so the
 * browser's own timezone never shifts the label.
 */
export function formatLocalStamp(local: string, locale: Locale = 'en'): string {
  const m = /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2})/.exec(local);
  if (!m) return local;
  const month = Number(m[2]);
  if (month < 1 || month > 12) return local;
  // Parse only wall-clock parts: the suffix already describes the org zone.
  // Keep the legacy permissive day/time fallback (no Date normalization).
  const monthName = formatDateShapeFor(locale, new Date(Date.UTC(2026, month - 1, 1)), 'monthShort', 'UTC');
  return translate(locale, 'usage.localStamp', {
    month: monthName, day: Number(m[3]), time: `${m[4]}:${m[5]}`,
  });
}

export function formatWindow(window: { start_local: string; end_local: string }, locale: Locale = 'en'): string {
  return `${formatLocalStamp(window.start_local, locale)} – ${formatLocalStamp(window.end_local, locale)}`;
}

/** A UTC instant rendered in the response's timezone ("Sep 29, 14:03"). */
export function formatInstant(iso: string, timeZone: string, locale: Locale = 'en'): string {
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return iso;
  try {
    return formatDateShapeFor(locale, date, 'monthDayClock24', timeZone);
  } catch {
    return iso;
  }
}

export function formatCount(n: number, locale: Locale = 'en'): string {
  return formatCountFor(locale, n);
}

/** Token figures: compact, with the exact value kept for titles / AT. */
export function formatTokenValue(n: number, locale: Locale = 'en'): string {
  return formatTokensFor(locale, n);
}

export function formatExactTokens(n: number, locale: Locale = 'en'): string {
  return n.toLocaleString(locale, { maximumFractionDigits: 1 });
}

/** Recorded agent runtime: "4h 10m", "45m", "38s". */
export function formatDuration(totalSeconds: number, locale: Locale = 'en'): string {
  const s = Math.round(Math.abs(totalSeconds));
  if (s < 60) return translate(locale, 'usage.seconds', { n: formatCount(s, locale) });
  const minutes = Math.floor(s / 60);
  const hours = Math.floor(minutes / 60);
  const rest = minutes % 60;
  if (hours === 0) return translate(locale, 'usage.minutes', { n: formatCount(minutes, locale) });
  return translate(locale, 'usage.hoursMinutes', { hours: locale === 'en' ? String(hours) : formatCount(hours, locale), minutes: String(rest).padStart(2, '0') });
}

/** A server-supplied rate in [0, 1] as a whole percentage. */
export function formatRate(rate: number, locale: Locale = 'en'): string {
  const pct = rate * 100;
  if (pct > 0 && pct < 1) return '<1%';
  return `${formatCount(Math.round(pct), locale)}%`;
}

const MINUS = '−';

export function signed(text: string, negative: boolean): string {
  return `${negative ? MINUS : '+'}${text}`;
}

/** A server-supplied percent movement ("−8%", "+14%", "+0.4%"). */
export function formatPercentDelta(value: number, locale: Locale = 'en'): string {
  const abs = Math.abs(value);
  const text = abs < 10 ? (Math.round(abs * 10) / 10).toLocaleString(locale) : locale === 'en' ? String(Math.round(abs)) : formatCount(Math.round(abs), locale);
  return signed(`${text}%`, value < 0);
}

/**
 * Plain-language explanations for the daemon's `withheld_reason` codes.
 * Raw codes are never rendered.
 */
export function withheldExplanation(reason: string | null, locale: Locale = 'en'): string {
  switch (reason) {
    case 'usage_coverage_below_95_percent':
      return translate(locale, 'usage.withheldCoverage');
    case 'unattributed_lifecycle_runs':
      return translate(locale, 'usage.withheldUnattributed');
    case 'invalid_baseline':
      return translate(locale, 'usage.withheldBaseline');
    case 'reply_outcome_not_recorded':
      return translate(locale, 'usage.withheldReply');
    default:
      return translate(locale, 'usage.withheldFallback');
  }
}

export const ROW_LEVEL_WITHHELD_REASONS: ReadonlySet<string> = new Set([
  'usage_coverage_below_95_percent',
  'unattributed_lifecycle_runs',
]);
