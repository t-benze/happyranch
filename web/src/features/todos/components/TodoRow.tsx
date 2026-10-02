/**
 * TodoRow — a single schedule row in the Todos list.
 *
 * The entire row is a navigation link to /orgs/:slug/todos/:scheduleId.
 * No nested interactive controls.
 *
 * Time rendering uses the stored IANA timezone (schedule.timezone).
 */
import { Link, useParams } from 'react-router-dom'
import type { ScheduleRecord } from '@/lib/api/types'
import { useTranslation } from '@/hooks/i18n'
import type { Locale } from '@/lib/i18n'
import { formatCountFor } from '@/lib/i18n/format'
import { StatusPill } from './StatusPill'
import { shortDayLabel, type Translate } from '../strings'
import { formatFireAtInTz, formatReviewDate } from '../timezone'
import { formatRecurringRule } from '../recurrence'
import { cn } from '@/lib/utils'

interface TodoRowProps {
  schedule: ScheduleRecord
}

/** Describe the schedule type in a concise, status-aware human line using the stored tz. */
function scheduleLine(s: ScheduleRecord, t: Translate, locale: Locale): string {
  const tz = s.timezone || 'UTC'
  const isPast = ['fired', 'failed', 'timeout', 'expired', 'cancelled'].includes(s.status)

  if (s.kind === 'one_shot') {
    if (s.fire_at) {
      const formatted = formatFireAtInTz(s.fire_at, tz, locale)
      if (formatted !== s.fire_at) {
        return t('todos.schedule.onceOn', { when: formatted })
      }
    }
    return t('todos.kind.oneShot')
  }

  if (s.kind === 'recurring') return formatRecurringRule(s.recurrence, tz, t)

  const day = shortDayLabel(String(s.recurrence?.day ?? ''), t)
  const time = String(s.recurrence?.time ?? '')
  const line = t(isPast ? 'todos.schedule.wasEvery' : 'todos.schedule.every', { day, time })
  if (s.indefinite) {
    return `${line} · ${t('todos.indefinite')}`
  }
  return line
}

/** Expiry / review line for the row. */
function expiryLine(s: ScheduleRecord, t: Translate, locale: Locale): string | null {
  if (s.status === 'expired' && s.expires_at) {
    const d = new Date(s.expires_at)
    if (!Number.isNaN(d.getTime())) {
      return t('todos.row.expiredOn', { date: formatReviewDate(d, locale) })
    }
    return t('todos.row.expired')
  }
  if (s.indefinite) return t('todos.indefinite')
  if (s.expires_at) {
    const d = new Date(s.expires_at)
    if (!Number.isNaN(d.getTime())) {
      return t('todos.row.reviewBy', { date: formatReviewDate(d, locale) })
    }
  }
  return null
}

/** Initials from agent name for the owner avatar. */
function agentInitials(name: string): string {
  return name
    .split(/[_\-.]+/)
    .slice(0, 2)
    .map((p) => p[0]?.toUpperCase() ?? '')
    .join('')
}

export function TodoRow({ schedule }: TodoRowProps): JSX.Element {
  const { slug } = useParams<{ slug: string }>()
  const { t, locale } = useTranslation()
  const isTerminal = ['fired', 'expired', 'cancelled', 'failed', 'timeout'].includes(
    schedule.status,
  )
  const showNextFire = schedule.status === 'armed' || schedule.status === 'firing'
  const tz = schedule.timezone || 'UTC'

  const fireAtDisplay = showNextFire && schedule.fire_at
    ? formatFireAtInTz(schedule.fire_at, tz, locale)
    : ''

  const expiry = expiryLine(schedule, t, locale)

  return (
    <Link
      to={`/orgs/${slug}/todos/${schedule.schedule_id}`}
      className={cn(
        'flex w-full flex-col gap-2.5 px-5 py-4 text-left transition-colors hover:bg-bg-subtle',
        'border-b border-border-subtle last:border-b-0',
      )}
    >
      <div className="flex items-start justify-between gap-4">
        <div className="flex min-w-0 items-center gap-2.5">
          <StatusPill status={schedule.status} />
          <span
            className={cn(
              'truncate text-sm font-semibold text-fg',
              isTerminal && 'text-fg-muted',
            )}
          >
            {schedule.normalized_brief}
          </span>
        </div>
        {showNextFire && (
          <div className="flex shrink-0 flex-col items-end">
            <span className="text-fg-subtle text-2xs leading-tight font-normal tracking-wider uppercase">
              {t('todos.row.nextFire')}
            </span>
            <span className="text-fg text-sm leading-tight font-semibold tabular-nums">
              {schedule.status === 'firing' ? t('todos.row.firingNow') : fireAtDisplay}
            </span>
          </div>
        )}
      </div>

      <div className="flex items-center justify-between gap-2">
        <div className="text-fg-muted flex min-w-0 items-center gap-1.5 text-xs">
          <span className="inline-flex items-center gap-1">
            <span
              aria-hidden="true"
              className="bg-tier-green-tint text-status-open inline-flex size-4 shrink-0 items-center justify-center rounded text-xs font-semibold"
            >
              {agentInitials(schedule.agent_name)}
            </span>
            <span className="truncate">{schedule.agent_name}</span>
          </span>
          <span aria-hidden="true" className="text-fg-subtle">
            ·
          </span>
          <span className="truncate">{scheduleLine(schedule, t, locale)}</span>
        </div>
        <div className="text-fg-subtle flex shrink-0 items-center gap-3 text-xs">
          {expiry && <span>{expiry}</span>}
          {schedule.fire_count > 0 && (
            <span>
              {t('todos.row.runs', { count: formatCountFor(locale, schedule.fire_count) })}
            </span>
          )}
          <span className="text-fg-subtle font-mono text-xs">
            {schedule.schedule_id}
          </span>
        </div>
      </div>
    </Link>
  )
}
