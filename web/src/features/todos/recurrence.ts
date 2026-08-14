import type { ScheduleRecurrence } from '@/lib/api/types'

const DAY_NAMES: Record<string, string> = {
  MO: 'Monday', TU: 'Tuesday', WE: 'Wednesday', TH: 'Thursday',
  FR: 'Friday', SA: 'Saturday', SU: 'Sunday',
}

const UNIT_NAMES: Record<string, string> = {
  DAILY: 'day', WEEKLY: 'week', MONTHLY: 'month', YEARLY: 'year',
}

export function formatRecurringRule(rule: ScheduleRecurrence | null, timezone: string): string {
  if (!rule) return 'Recurring schedule'
  const interval = Number(rule.interval ?? 1)
  const unit = UNIT_NAMES[String(rule.freq)] ?? 'cycle'
  const every = `Every ${interval === 1 ? '' : `${interval} `}${unit}${interval === 1 ? '' : 's'}`
  let selector = ''
  if (rule.freq === 'WEEKLY' && Array.isArray(rule.byday)) {
    selector = ` on ${rule.byday.map((day) => DAY_NAMES[day] ?? day).join(', ')}`
  }
  if (rule.freq === 'MONTHLY') {
    if (rule.bymonthday) selector = ` on day ${rule.bymonthday}`
    else if (rule.ordinal && Array.isArray(rule.byday)) {
      selector = ` on the ${rule.ordinal} ${DAY_NAMES[rule.byday[0]] ?? rule.byday[0]}`
    }
  }
  const time = rule.time ? ` at ${rule.time} ${timezone}` : ` ${timezone}`
  const ending = rule.until
    ? ` · Ends on ${rule.until}`
    : rule.count ? ` · Ends after ${rule.count} occurrences` : ' · Ends never'
  return `${every}${selector}${time}${ending}`
}
