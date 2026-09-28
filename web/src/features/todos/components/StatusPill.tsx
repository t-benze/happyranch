/**
 * StatusPill — schedule-status badge for the Todos surface.
 *
 * Uses the shared semantic-tone vocabulary while retaining the approved Todos
 * pill geometry and armed/firing LED behavior.
 */
import type { ScheduleStatus } from '@/lib/api/types'
import { toneClass } from '@/design-system/patterns/semanticTone'
import { statusLabel } from '../strings'

interface StatusPillProps {
  status: ScheduleStatus
}

const LED_STATUSES: Set<ScheduleStatus> = new Set(['armed', 'firing'])

export function StatusPill({ status }: StatusPillProps): JSX.Element {
  const cls = toneClass(status)
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
