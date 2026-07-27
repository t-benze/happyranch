/**
 * StatusPill — schedule-status badge for the Todos surface.
 * Reuses the semantic tone vocabulary and existing design-system token classes.
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
      className={`inline-flex items-center gap-1.5 rounded-full px-2.5 py-0.5 text-xs font-semibold leading-snug ${cls}`}
    >
      {showLed && (
        <span
          className="inline-block h-1.5 w-1.5 rounded-full bg-current opacity-70"
          aria-hidden
        />
      )}
      {label}
    </span>
  );
}
