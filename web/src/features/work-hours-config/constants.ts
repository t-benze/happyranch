/**
 * Static UI constants for the Work-Hours Config editors.
 *
 * These drive PICKERS only — the SERVER is the single validation authority. The
 * timezone list is a convenience so the founder can pick a common zone; an
 * out-of-list value (e.g. typed elsewhere) is still rejected server-side with a
 * 422. The interval is a free-form text input in BOTH modes — the divides-24h
 * rule lives only in the server (_build_org_config), never on the client.
 */

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
