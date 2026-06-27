/**
 * Static UI constants for the Work-Hours Config editors.
 *
 * These drive PICKERS only — the SERVER is the single validation authority. The
 * interval divisor list and timezone list are conveniences so the founder
 * can't easily type an obviously-bad value; an out-of-list value (e.g. typed
 * elsewhere) is still rejected server-side with a 422.
 */

/** Divisors of 24h offered in continuous mode (spec §5 guardrail). */
export const INTERVAL_DIVISORS = [
  '15m',
  '30m',
  '1h',
  '2h',
  '3h',
  '4h',
  '6h',
  '8h',
  '12h',
  '24h',
] as const;

export const DAYS = ['mon', 'tue', 'wed', 'thu', 'fri', 'sat', 'sun'] as const;
export type Day = (typeof DAYS)[number];

/** Resolve the available IANA timezone list at runtime when the engine exposes
 * `Intl.supportedValuesOf`, otherwise fall back to a curated common set. The
 * server validates the chosen zone via ZoneInfo regardless. */
export function ianaTimezones(): string[] {
  const intl = Intl as unknown as {
    supportedValuesOf?: (key: string) => string[];
  };
  if (typeof intl.supportedValuesOf === 'function') {
    try {
      const zones = intl.supportedValuesOf('timeZone');
      if (Array.isArray(zones) && zones.length > 0) return zones;
    } catch {
      // fall through to the curated list
    }
  }
  return COMMON_TIMEZONES;
}

export const COMMON_TIMEZONES = [
  'UTC',
  'America/Los_Angeles',
  'America/Denver',
  'America/Chicago',
  'America/New_York',
  'America/Sao_Paulo',
  'Europe/London',
  'Europe/Paris',
  'Europe/Berlin',
  'Europe/Moscow',
  'Asia/Dubai',
  'Asia/Kolkata',
  'Asia/Shanghai',
  'Asia/Singapore',
  'Asia/Tokyo',
  'Australia/Sydney',
  'Pacific/Auckland',
];
