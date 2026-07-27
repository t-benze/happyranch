/**
 * TodoRow — a single schedule row in the Todos list.
 * Pure presentational; actions call back to the parent.
 */
import { useParams } from 'react-router-dom'
import type { ScheduleRecord } from '@/lib/api/types'
import { StatusPill } from './StatusPill'
import { TODO_STRINGS } from '../strings'
import { cn } from '@/lib/utils'

interface TodoRowProps {
  schedule: ScheduleRecord
  onClick: () => void
}

/** Format a UTC fire_at to a display string: "Sat Jul 25 · 09:00" */
function formatFireAt(fireAt: string): string {
  try {
    const d = new Date(fireAt)
    if (isNaN(d.getTime())) return fireAt
    return d.toLocaleDateString('en-US', {
      weekday: 'short',
      month: 'short',
      day: 'numeric',
      timeZone: 'UTC',
    }) + ' · ' + d.toLocaleTimeString('en-US', {
      hour: '2-digit',
      minute: '2-digit',
      timeZone: 'UTC',
    })
  } catch {
    return fireAt
  }
}

/** Describe the schedule type in a concise human line. */
function scheduleLine(s: ScheduleRecord): string {
  if (s.kind === 'one_shot') {
    const d = s.fire_at ? new Date(s.fire_at) : null
    if (d && !isNaN(d.getTime())) {
      return `Once on ${d.toLocaleDateString('en-US', {
        month: 'short',
        day: 'numeric',
        year: 'numeric',
        timeZone: 'UTC',
      })} at ${d.toLocaleTimeString('en-US', {
        hour: '2-digit',
        minute: '2-digit',
        timeZone: 'UTC',
      })}`
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

export function TodoRow({ schedule, onClick }: TodoRowProps): JSX.Element {
  const { slug } = useParams<{ slug: string }>()
  const isTerminal = ['fired', 'expired', 'cancelled', 'failed', 'timeout'].includes(
    schedule.status,
  )
  const showNextFire = schedule.status === 'armed' || schedule.status === 'firing'
  const fireAtDisplay = schedule.fire_at ? formatFireAt(schedule.fire_at) : ''
  const expiry = expiryLine(schedule)

  return (
    <button
      type="button"
      onClick={onClick}
      className={cn(
        'flex w-full flex-col gap-2.5 px-5 py-4 text-left transition-colors hover:bg-bg-subtle',
        'border-b border-border-subtle last:border-b-0',
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
          <a
            href={`/orgs/${slug}/todos/${schedule.schedule_id}`}
            onClick={(e) => {
              e.stopPropagation()
              // Let the Link navigate naturally
            }}
            className="font-mono text-xs text-fg-subtle hover:text-fg-muted"
          >
            {schedule.schedule_id}
          </a>
        </div>
      </div>
    </button>
  )
}
