import type { ScheduleRecurrence } from '@/lib/api/types'
import type { MessageKey } from '@/lib/i18n'
import type { Translate } from './strings'

/** RRULE weekday token -> catalog key; unknown tokens render verbatim. */
export const DAY_KEYS: Record<string, MessageKey> = {
  MO: 'todos.weekday.mon', TU: 'todos.weekday.tue', WE: 'todos.weekday.wed', TH: 'todos.weekday.thu',
  FR: 'todos.weekday.fri', SA: 'todos.weekday.sat', SU: 'todos.weekday.sun',
}

/** Monthly ordinal token -> catalog key; unknown tokens render verbatim. */
export const ORDINAL_KEYS: Record<string, MessageKey> = {
  first: 'todos.ordinal.first', second: 'todos.ordinal.second', third: 'todos.ordinal.third',
  fourth: 'todos.ordinal.fourth', fifth: 'todos.ordinal.fifth', last: 'todos.ordinal.last',
}

const EVERY_KEYS: Record<string, [MessageKey, MessageKey]> = {
  DAILY: ['todos.recurrence.every.daily', 'todos.recurrence.everyN.daily'],
  WEEKLY: ['todos.recurrence.every.weekly', 'todos.recurrence.everyN.weekly'],
  MONTHLY: ['todos.recurrence.every.monthly', 'todos.recurrence.everyN.monthly'],
  YEARLY: ['todos.recurrence.every.yearly', 'todos.recurrence.everyN.yearly'],
}
const CYCLE_KEYS: [MessageKey, MessageKey] = ['todos.recurrence.every.cycle', 'todos.recurrence.everyN.cycle']

function lookup(map: Record<string, MessageKey>, token: string, t: Translate): string {
  return Object.prototype.hasOwnProperty.call(map, token) ? t(map[token]) : token
}

export function dayName(token: string, t: Translate): string {
  return lookup(DAY_KEYS, token, t)
}

export function ordinalName(token: string, t: Translate): string {
  return lookup(ORDINAL_KEYS, token, t)
}

/**
 * Presentation of a native recurrence rule through catalog templates. Raw
 * daemon values (time strings, IANA timezone ids, `until` dates, unknown
 * tokens) are interpolated verbatim.
 */
export function formatRecurringRule(
  rule: ScheduleRecurrence | null,
  timezone: string,
  t: Translate,
): string {
  if (!rule) return t('todos.recurrence.fallback')
  const interval = Number(rule.interval ?? 1)
  const freq = String(rule.freq)
  const [oneKey, manyKey] = Object.prototype.hasOwnProperty.call(EVERY_KEYS, freq)
    ? EVERY_KEYS[freq]
    : CYCLE_KEYS
  let base = interval === 1 ? t(oneKey) : t(manyKey, { count: interval, interval: String(interval) })
  if (rule.freq === 'WEEKLY' && Array.isArray(rule.byday)) {
    base = t('todos.recurrence.onDays', {
      every: base,
      days: rule.byday.map((day) => dayName(day, t)).join(t('todos.recurrence.daySeparator')),
    })
  }
  if (rule.freq === 'MONTHLY') {
    if (rule.bymonthday) base = t('todos.recurrence.onMonthDay', { every: base, day: String(rule.bymonthday) })
    else if (rule.ordinal && Array.isArray(rule.byday)) {
      base = t('todos.recurrence.onOrdinal', {
        every: base,
        ordinal: ordinalName(String(rule.ordinal), t),
        weekday: dayName(rule.byday[0], t),
      })
    }
  }
  const timed = rule.time
    ? t('todos.recurrence.atTime', { rule: base, time: String(rule.time), timezone })
    : t('todos.recurrence.inTimezone', { rule: base, timezone })
  const ending = rule.until
    ? t('todos.recurrence.endsOn', { date: String(rule.until) })
    : rule.count
      ? t('todos.recurrence.endsAfter', { count: Number(rule.count) })
      : t('todos.recurrence.endsNever')
  return `${timed} · ${ending}`
}
