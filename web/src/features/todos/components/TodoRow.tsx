/**
 * TodoRow — a single schedule row in the Todos list.
 *
 * The entire row is a navigation link to /orgs/:slug/todos/:scheduleId.
 * No nested interactive controls — the schedule_id is rendered as plain
 * text, not as a child link.
 *
 * Time rendering uses the stored IANA timezone (schedule.timezone).
 */
import { Link, useParams } from 'react-router-dom'
import type { ScheduleRecord } from '@/lib/api/types'
import { StatusPill } from './StatusPill'
import { TODO_STRINGS } from '../strings'
import { formatFireAtInTz } from '../timezone'
import { cn } from '@/lib/utils'

interface TodoRowProps {
  schedule: ScheduleRecord
}

/** Describe the schedule type in a concise human line using the stored tz. */
function scheduleLine(s: ScheduleRecord): string {
  const tz = s.timezone || 'UTC'
  if (s.kind === 'one_shot') {
    if (s.fire_at) {
      const formatted = formatFireAtInTz(s.fire_at, tz)
      if (formatted !== s.fire_at) return `Once · ${formatted}`
    }
    return 'One-shot'
  }
  // weekly
  const day = s.recurrence?.day ?? ''
  const time = s.recurrence?.time ?? ''
  if (s.indefinite) {
    return `Every ${day} at ${time} · Indefinite`
  }
  return `Every ${day} at ${time}`
}

/** Expiry / review line for the row. */
function expiryLine(s: ScheduleRecord): string | null {
  if (s.status === 'expired' && s.expires_at) {
    const d = new Date(s.expires_at)
    if (!isNaN(d.getTime())) {
      return `Expired ${d.toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' })}`
    }
    return 'Expired'
  }
  if (s.indefinite) return TODO_STRINGS.indefiniteLabel
  if (s.expires_at) {
    const d = new Date(s.expires_at)
    if (!isNaN(d.getTime())) {
      return `${TODO_STRINGS.reviewByLabel} ${d.toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' })}`
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
  const isTerminal = ['fired', 'expired', 'cancelled', 'failed', 'timeout'].includes(
    schedule.status,
  )
  const showNextFire = schedule.status === 'armed' || schedule.status === 'firing'
  const tz = schedule.timezone || 'UTC'

  // Fire-at display in the stored IANA timezone
  const fireAtDisplay = showNextFire && schedule.fire_at
    ? formatFireAtInTz(schedule.fire_at, tz)
    : ''

  const expiry = expiryLine(schedule)

  return (
    <Link
      to={`/orgs/${slug}/todos/${schedule.schedule_id}`}
      className={cn(
        'flex w-full flex-col gap-2.5 px-5 py-4 text-left transition-colors hover:bg-bg-subtle',
        'border-b border-border-subtle last:border-b-0',
        'block', // ensure full-width clickable area
      )}
    >
      {/* Row top: status pill + commitment + next-fire */}
      <div className="flex items-start justify-between gap-4">
        <div className="flex min-w-0 items-center gap-2.5">
          <StatusPill status={schedule.status} />
          <span
            className={cn(
              'truncate text-[0.9375rem] font-semibold text-fg',
              isTerminal && 'text-fg-muted',
            )}
          >
            {schedule.normalized_brief}
          </span>
        </div>
        {showNextFire && (
          <div className="flex shrink-0 flex-col items-end">
            <span className="text-xs font-normal text-fg-subtle leading-tight">
              {schedule.status === 'firing'
                ? TODO_STRINGS.nextFireLabel
                : TODO_STRINGS.nextFireLabel}
            </span>
            <span className="text-sm font-semibold text-fg leading-tight tabular-nums">
              {schedule.status === 'firing' ? TODO_STRINGS.firingNow : fireAtDisplay}
            </span>
          </div>
        )}
      </div>

      {/* Row bottom: owner chip + schedule description + meta */}
      <div className="flex items-center justify-between gap-2">
        <div className="flex items-center gap-1.5 text-xs text-fg-muted min-w-0">
          {/* Owner chip: avatar initials + name */}
          <span className="inline-flex items-center gap-1">
            <span
              aria-hidden
              className="inline-flex size-4 shrink-0 items-center justify-center rounded bg-tier-green-tint text-xs font-semibold text-status-open"
            >
              {agentInitials(schedule.agent_name)}
            </span>
            <span className="truncate">{schedule.agent_name}</span>
          </span>
          <span aria-hidden className="text-fg-subtle">·</span>
          <span className="truncate">{scheduleLine(schedule)}</span>
        </div>
        <div className="flex shrink-0 items-center gap-3 text-xs text-fg-subtle">
          {expiry && <span>{expiry}</span>}
          {schedule.fire_count > 0 && (
            <span>{schedule.fire_count} {TODO_STRINGS.runsLabel}</span>
          )}
          <span className="font-mono text-xs text-fg-subtle">
            {schedule.schedule_id}
          </span>
        </div>
      </div>
    </Link>
  )
}
