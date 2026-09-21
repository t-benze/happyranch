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
