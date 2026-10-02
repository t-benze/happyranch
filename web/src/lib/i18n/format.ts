/**
 * @/lib/i18n/format — explicit-locale display formatters (THR-118 W1).
 *
 * `@/lib/format` stays the ONE canonical formatter: the English path here
 * delegates to `formatTokens`/`formatCount` rather than forking a competing
 * implementation. W1 defines the explicit-locale interfaces only; broad caller
 * migration belongs to W2/W4.
 *
 * Display metrics only. Exact identifiers/config values/machine inputs keep
 * their literal form; schedule-timezone *calculations* are untouched and only
 * display output is localized.
 */
import { formatCount, formatTokens } from '@/lib/format';
import type { Locale } from './locale';

function trimCompact(value: number): string {
  const fixed = value.toFixed(1);
  return fixed.endsWith('.0') ? fixed.slice(0, -2) : fixed;
}

/**
 * Compact metric formatter. English keeps the canonical K/M suffix contract;
 * Simplified Chinese uses the local 万 (10^4) and 亿 (10^8) units. Below the
 * first unit boundary the exact integer is returned verbatim.
 */
export function formatTokensFor(locale: Locale, n: number): string {
  if (locale === 'en') return formatTokens(n);
  const abs = Math.abs(n);
  const sign = n < 0 ? '-' : '';
  if (abs >= 100_000_000) return `${sign}${trimCompact(abs / 100_000_000)}亿`;
  if (abs >= 10_000) return `${sign}${trimCompact(abs / 10_000)}万`;
  return String(n);
}

/**
 * Exact, grouped integer counter. Never compacts: a count of 1000 renders
 * `1,000` in both locales. Delegates to the ONE canonical `formatCount` with
 * its explicit-locale argument, so the display locale is honored even when the
 * host `Intl` default is a different language (e.g. `LC_ALL=de_DE`).
 */
export function formatCountFor(locale: Locale, n: number): string {
  return formatCount(n, locale);
}

export interface LocaleDateFormatOptions {
  /** Required explicit IANA timezone — output must not depend on the host. */
  timeZone: string;
  dateStyle?: 'short' | 'medium' | 'long';
  timeStyle?: 'short' | 'medium';
}

/** Deterministic date/time display with an explicit locale and timezone. */
export function formatDateTimeFor(
  locale: Locale,
  value: Date | number,
  options: LocaleDateFormatOptions,
): string {
  const date = typeof value === 'number' ? new Date(value) : value;
  return new Intl.DateTimeFormat(locale, {
    dateStyle: options.dateStyle ?? 'medium',
    timeStyle: options.timeStyle ?? 'short',
    timeZone: options.timeZone,
  }).format(date);
}

/**
 * Centrally owned date/time display shapes (THR-118 W4b). Feature code picks a
 * named shape instead of hand-assembling `Intl` options, so every visible
 * date/time on a translated surface goes through this module.
 */
const DATE_SHAPES = {
  /** `Wed, Jun 10, 2026` / `2026年6月10日周三` */
  weekdayDate: { weekday: 'short', month: 'short', day: 'numeric', year: 'numeric' },
  /** `Wed, Jun 10` / `6月10日周三` */
  weekdayMonthDay: { weekday: 'short', month: 'short', day: 'numeric' },
  /** `Jun 10` / `6月10日` */
  monthDay: { month: 'short', day: 'numeric' },
  /** `Jun 10, 2026` / `2026年6月10日` */
  monthDayYear: { month: 'short', day: 'numeric', year: 'numeric' },
  /** `Wednesday` / `星期三` */
  weekdayLong: { weekday: 'long' },
  /** 24-hour `14:05` */
  clock24: { hour: '2-digit', minute: '2-digit', hour12: false },
  /** 24-hour `14:05:09` */
  clock24Seconds: { hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false },
  /** `Jun 10, 02:05 PM` / `6月10日 14:05` */
  monthDayTime: { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' },
  /** `Jun 10, 2026, 02:05 PM` / `2026年6月10日 14:05` */
  dateTime: { year: 'numeric', month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' },
} as const satisfies Record<string, Intl.DateTimeFormatOptions>;

export type LocaleDateShape = keyof typeof DATE_SHAPES;

/**
 * Format `value` in a named display shape with an explicit locale. `timeZone`
 * is an IANA id; omit it only where the surface deliberately shows the
 * viewer's local time. An invalid date renders `Invalid Date` (the
 * `Date#toLocaleString` behaviour) rather than throwing; an invalid timezone
 * still throws `RangeError`, as `Intl` does.
 */
export function formatDateShapeFor(
  locale: Locale,
  value: Date | number,
  shape: LocaleDateShape,
  timeZone?: string,
): string {
  const date = typeof value === 'number' ? new Date(value) : value;
  if (Number.isNaN(date.getTime())) return 'Invalid Date';
  return new Intl.DateTimeFormat(locale === 'en' ? 'en-US' : locale, {
    ...DATE_SHAPES[shape],
    ...(timeZone ? { timeZone } : {}),
  }).format(date);
}
