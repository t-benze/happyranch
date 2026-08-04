/**
 * StatusPill — schedule-status badge for the Todos surface.
 *
 * Uses Todos-local exact-color matches to the approved THR-105 reference
 * (web/scripts/screenshot-harness/reference/reference-todos-list-light.png)
 * without mutating shared design-system tokens.
 */
import type { ScheduleStatus } from '@/lib/api/types'
import { statusLabel, statusPillClass } from '../strings'

interface StatusPillProps {
  status: ScheduleStatus
}

const LED_STATUSES: Set<ScheduleStatus> = new Set(['armed', 'firing'])

export function StatusPill({ status }: StatusPillProps): JSX.Element {
  const cls = statusPillClass(status)
  const label = statusLabel(status)
  const showLed = LED_STATUSES.has(status)
  return (
    <span
      className={`text-overline inline-flex items-center gap-1.5 rounded-full px-2.5 py-0.5 leading-snug font-semibold ${cls}`}
    >
      {showLed && (
        <span
          className="inline-block h-1.5 w-1.5 rounded-full bg-current opacity-80"
          aria-hidden="true"
        />
      )}
      {label}
    </span>
  )
}
