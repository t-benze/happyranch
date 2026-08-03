/**
 * Timezone-aware date/time utilities for the Todos feature.
 *
 * Uses Intl.DateTimeFormat with IANA timezone names (formatToParts) to
 * resolve offsets and compute the next weekly occurrence in a named timezone.
 *
 * The contract mirrors the backend schedule_rules behaviour:
 * nextWeeklyOccurrence walks at most 366 days forward and returns a UTC
 * ISO-8601 instant (no sub-second fraction, Z suffix) or null.
 */

const WEEKDAY_NAMES_SHORT = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'] as const
const WEEKDAY_NAMES_LONG = [
  'Sunday',
  'Monday',
  'Tuesday',
  'Wednesday',
  'Thursday',
  'Friday',
  'Saturday',
] as const

/** Return the timezone offset in minutes (positive = ahead of UTC) for a
 *  given Date at a given IANA timezone. */
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

/** Decompose a Date into local calendar parts as seen in `tz`. */
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
  const weekday = WEEKDAY_NAMES_SHORT.indexOf(
    get('weekday') as (typeof WEEKDAY_NAMES_SHORT)[number],
  )
  return {
    year: parseInt(get('year'), 10),
    month: parseInt(get('month'), 10) - 1,
    day: parseInt(get('day'), 10),
    weekday: weekday >= 0 ? weekday : 0,
    hour: parseInt(get('hour'), 10),
    minute: parseInt(get('minute'), 10),
  }
}

/**
 * Serialize a one-shot local date/time (YYYY-MM-DD + HH:MM) in IANA
 * timezone `tz` to a UTC ISO-8601 instant string (e.g.
 * "2026-08-01T01:00:00Z") or null.
 *
 * The local date/time is interpreted in the named timezone, including DST.
 * If the supplied wall time does not exist in that timezone (a DST gap),
 * the function returns null so the caller can surface a validation error
 * instead of emitting an unchecked UTC candidate.
 *
 * Ambiguous local times (DST fold) resolve to the first matching instant;
 * the returned instant is verified to render back to the supplied wall time.
 */
export function serializeOneShotInTz(
  dateStr: string,
  timeStr: string,
  tz: string,
): string | null {
  const [year, month, day] = dateStr.split('-').map(Number)
  const [hour, minute] = timeStr.split(':').map(Number)
  if (
    Number.isNaN(year) ||
    Number.isNaN(month) ||
    Number.isNaN(day) ||
    Number.isNaN(hour) ||
    Number.isNaN(minute)
  ) {
    return null
  }

  try {
    new Intl.DateTimeFormat('en-US', { timeZone: tz }).format(new Date())
  } catch {
    return null
  }

  // Search the +/- 25 hour window around the naive UTC interpretation.
  // This window comfortably covers every current IANA offset (max ~±14 h)
  // and all DST shifts, so a real instant is always found if it exists.
  const naive = Date.UTC(year, month - 1, day, hour, minute, 0)
  for (let deltaMinutes = -25 * 60; deltaMinutes <= 25 * 60; deltaMinutes++) {
    const candidate = naive + deltaMinutes * 60_000
    const parts = tzParts(new Date(candidate), tz)
    if (
      parts.year === year &&
      parts.month === month - 1 &&
      parts.day === day &&
      parts.hour === hour &&
      parts.minute === minute
    ) {
      return new Date(candidate).toISOString().replace(/\.\d{3}Z$/, 'Z')
    }
  }

  // No instant renders back to the requested local time (nonexistent wall
  // time, e.g. a DST gap). Return null so the caller blocks the mutation.
  return null
}

/**
 * Compute the next occurrence of `day` at `timeStr` (HH:MM) in IANA
 * timezone `tz`, strictly AFTER `after` (default: now). Walks at most 366
 * days. Returns a UTC ISO-8601 string (e.g. "2026-08-01T01:00:00Z") or null.
 */
export function nextWeeklyOccurrence(
  day: string,
  timeStr: string,
  tz: string,
  after: Date = new Date(),
): string | null {
  const found = WEEKDAY_NAMES_LONG.find((n) =>
    n.toLowerCase().startsWith(day.toLowerCase().slice(0, 3)),
  )
  const targetDay = found ? WEEKDAY_NAMES_LONG.indexOf(found) : -1
  if (targetDay < 0) return null

  const [hour, minute] = timeStr.split(':').map(Number)
  if (Number.isNaN(hour) || Number.isNaN(minute)) return null

  try {
    new Intl.DateTimeFormat('en-US', { timeZone: tz }).format(new Date())
  } catch {
    return null
  }

  const afterParts = tzParts(after, tz)

  for (let d = 0; d < 366; d++) {
    const probe = new Date(after.getTime() + d * 86_400_000)
    const probeParts = tzParts(probe, tz)

    if (probeParts.weekday !== targetDay) continue

    const afterKey = afterParts.year * 10_000 + (afterParts.month + 1) * 100 + afterParts.day
    const probeKey = probeParts.year * 10_000 + (probeParts.month + 1) * 100 + probeParts.day

    if (probeKey === afterKey) {
      if (hour < afterParts.hour) continue
      if (hour === afterParts.hour && minute <= afterParts.minute) continue
    }
    if (probeKey < afterKey) continue

    const naive = Date.UTC(probeParts.year, probeParts.month, probeParts.day, hour, minute, 0)
    const offset = tzOffsetMinutes(new Date(naive), tz)
    const real = naive - offset * 60_000

    if (real > after.getTime()) {
      return new Date(real).toISOString().replace(/\.\d{3}Z$/, 'Z')
    }
  }

  return null
}

/** Format a UTC ISO-8601 datetime string in the given IANA timezone. */
export function formatFireAtInTz(isoString: string, tz: string): string {
  try {
    const d = new Date(isoString)
    if (Number.isNaN(d.getTime())) return isoString
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

/** Format a Date for preview in a given IANA timezone. */
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

/** Format a UTC ISO-8601 timestamp as an abbreviated date in the given TZ. */
export function formatDateShortInTz(isoString: string, tz: string): string {
  try {
    const d = new Date(isoString)
    if (Number.isNaN(d.getTime())) return isoString
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

/** Format a UTC ISO-8601 timestamp as time-only in the given TZ. */
export function formatTimeInTz(isoString: string, tz: string): string {
  try {
    const d = new Date(isoString)
    if (Number.isNaN(d.getTime())) return isoString
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
