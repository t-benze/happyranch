/**
 * Timezone-aware date/time utilities for the Todos feature.
 *
 * Pure JS, no external timezone library — uses Intl.DateTimeFormat with
 * IANA timezone names (formatToParts) to resolve offsets and compute the
 * next weekly occurrence in a named timezone.
 *
 * The contract mirrors runtime/orchestrator/schedule_rules.py:
 * next_weekly_occurrence walks at most 366 days forward and returns a UTC
 * ISO-8601 instant (no sub-second fraction, Z suffix) or null.
 */

const WEEKDAY_NAMES_SHORT = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'] as const
const WEEKDAY_NAMES_LONG = [
  'Sunday', 'Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday',
] as const

/** Return the timezone offset in minutes (positive = ahead of UTC) for a
 *  given Date at a given IANA timezone.  Uses formatToParts with
 *  timeZoneName:'longOffset'. */
function tzOffsetMinutes(date: Date, tz: string): number {
  const parts = new Intl.DateTimeFormat('en-US', {
    timeZone: tz,
    timeZoneName: 'longOffset',
    hour12: false,
  } as Intl.DateTimeFormatOptions).formatToParts(date)
  const name = parts.find((p) => p.type === 'timeZoneName')?.value ?? 'GMT'
  const m = name.match(/GMT([+-]\d{2}):(\d{2})/)
  if (!m) return 0
  const sign = m[1].startsWith('-') ? -1 : 1
  return parseInt(m[1], 10) * 60 + sign * parseInt(m[2], 10)
}

/** Decompose a Date into { year, month(0-11), day, weekday(0-6 Sun-Sat),
 *  hour, minute } as seen in `tz`. */
function tzParts(date: Date, tz: string) {
  const fmt = new Intl.DateTimeFormat('en-US', {
    timeZone: tz,
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    weekday: 'short',
    hour: '2-digit',
    minute: '2-digit',
    hour12: false,
  } as Intl.DateTimeFormatOptions)
  const parts = fmt.formatToParts(date)
  const get = (type: string) => parts.find((p) => p.type === type)?.value ?? '0'
  const weekday = WEEKDAY_NAMES_SHORT.indexOf(get('weekday') as (typeof WEEKDAY_NAMES_SHORT)[number])
  return {
    year: parseInt(get('year'), 10),
    month: parseInt(get('month'), 10) - 1, // JS Date months are 0-indexed
    day: parseInt(get('day'), 10),
    weekday: weekday >= 0 ? weekday : 0,
    hour: parseInt(get('hour'), 10),
    minute: parseInt(get('minute'), 10),
  }
}

/**
 * Compute the next occurrence of `day` at `timeStr` (HH:MM) in IANA
 * timezone `tz`, strictly AFTER `after` (default: now).  Walks at
 * most 366 days.  Returns a UTC ISO-8601 string (e.g.
 * "2026-08-01T01:00:00Z") or null if no occurrence found.
 */
export function nextWeeklyOccurrence(
  day: string,
  timeStr: string,
  tz: string,
  after: Date = new Date(),
): string | null {
  const found = WEEKDAY_NAMES_LONG.find(
      (n) => n.toLowerCase().startsWith(day.toLowerCase().slice(0, 3)),
    )
  const targetDay = found ? WEEKDAY_NAMES_LONG.indexOf(found) : -1
  if (targetDay < 0) return null

  const [hour, minute] = timeStr.split(':').map(Number)
  if (isNaN(hour) || isNaN(minute)) return null

  // Validate timezone — Intl.DateTimeFormat throws RangeError for invalid TZ.
  try {
    new Intl.DateTimeFormat('en-US', { timeZone: tz }).format(new Date())
  } catch {
    return null
  }

  const afterParts = tzParts(after, tz)

  for (let d = 0; d < 366; d++) {
    // Advance one calendar day in the target timezone by adding
    // 24 hours and re-computing the tz-local date.  This handles
    // DST transitions correctly because tzParts uses the IANA
    // timezone name.
    const probe = new Date(after.getTime() + d * 86_400_000)
    const probeParts = tzParts(probe, tz)

    if (probeParts.weekday !== targetDay) continue

    // Construct candidate date key for comparison (yyyyMMdd).
    const afterKey = afterParts.year * 10_000 + (afterParts.month + 1) * 100 + afterParts.day
    const probeKey = probeParts.year * 10_000 + (probeParts.month + 1) * 100 + probeParts.day

    // Same day → must be strictly after the current time in TZ.
    if (probeKey === afterKey) {
      if (hour < afterParts.hour) continue
      if (hour === afterParts.hour && minute <= afterParts.minute) continue
    }

    // Past day (shouldn't happen with +d*86400000, but guard).
    if (probeKey < afterKey) continue

    // Build the correct UTC instant.
    // 1. Build a naive UTC Date with the target local components.
    const naive = Date.UTC(probeParts.year, probeParts.month, probeParts.day, hour, minute, 0)

    // 2. Get the TZ offset at that naive moment.
    //    The offset may differ slightly from the probe offset near DST
    //    transitions; using the actual target time is more accurate.
    const offset = tzOffsetMinutes(new Date(naive), tz)

    // 3. Real UTC = naive - offset (offset positive = ahead of UTC).
    const real = naive - offset * 60_000

    // Only return if strictly after `after`.
    if (real > after.getTime()) {
      return new Date(real).toISOString().replace(/\.\d{3}Z$/, 'Z')
    }
  }

  return null
}

/**
 * Format a UTC ISO-8601 datetime string into a human-readable form in
 * the given IANA timezone.
 *
 * Returns e.g. "Sat Jul 25 · 17:00" or "Jul 25, 2026 at 17:00".
 */
export function formatFireAtInTz(
  isoString: string,
  tz: string,
): string {
  try {
    const d = new Date(isoString)
    if (isNaN(d.getTime())) return isoString
    const datePart = new Intl.DateTimeFormat('en-US', {
      timeZone: tz,
      weekday: 'short',
      month: 'short',
      day: 'numeric',
      year: 'numeric',
    }).format(d)
    const timePart = new Intl.DateTimeFormat('en-US', {
      timeZone: tz,
      hour: '2-digit',
      minute: '2-digit',
      hour12: false,
    }).format(d)
    return `${datePart} · ${timePart}`
  } catch {
    return isoString
  }
}

/**
 * Format a Date for preview in a given IANA timezone.
 * Returns e.g. "Sat, Jul 25, 2026 · 09:00".
 */
export function formatPreviewInTz(date: Date, tz: string): string {
  try {
    const datePart = new Intl.DateTimeFormat('en-US', {
      timeZone: tz,
      weekday: 'short',
      month: 'short',
      day: 'numeric',
      year: 'numeric',
    }).format(date)
    const timePart = new Intl.DateTimeFormat('en-US', {
      timeZone: tz,
      hour: '2-digit',
      minute: '2-digit',
      hour12: false,
    }).format(date)
    return `${datePart} · ${timePart}`
  } catch {
    return String(date)
  }
}

/**
 * Format a UTC ISO-8601 timestamp as an abbreviated date in the given TZ.
 * e.g. "Sat Jul 25".
 */
export function formatDateShortInTz(isoString: string, tz: string): string {
  try {
    const d = new Date(isoString)
    if (isNaN(d.getTime())) return isoString
    return new Intl.DateTimeFormat('en-US', {
      timeZone: tz,
      weekday: 'short',
      month: 'short',
      day: 'numeric',
    }).format(d)
  } catch {
    return isoString
  }
}

/**
 * Format a UTC ISO-8601 timestamp as time-only in the given TZ.
 * e.g. "09:00".
 */
export function formatTimeInTz(isoString: string, tz: string): string {
  try {
    const d = new Date(isoString)
    if (isNaN(d.getTime())) return isoString
    return new Intl.DateTimeFormat('en-US', {
      timeZone: tz,
      hour: '2-digit',
      minute: '2-digit',
      hour12: false,
    }).format(d)
  } catch {
    return isoString
  }
}
