/**
 * EditDialog — edit a schedule's timing.
 *
 * For weekly edits the outbound body carries:
 *   recurrence: { day, time, tz }
 *   timezone:  same as recurrence.tz
 *   fire_at:   the next weekly occurrence in the selected IANA tz
 */
import { useState, useEffect, useMemo } from 'react'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/design-system/primitives/Dialog'
import { Button } from '@/design-system/primitives/Button'
import { Input } from '@/design-system/primitives/Input'
import { Label } from '@/design-system/primitives/Label'
import {
  Select,
  SelectTrigger,
  SelectValue,
  SelectContent,
  SelectItem,
} from '@/design-system/primitives/Select'
import type { ScheduleRecord, ScheduleEditFields } from '@/lib/api/types'
import {
  nextWeeklyOccurrence,
  formatPreviewInTz,
  serializeOneShotInTz,
} from '../timezone'

const WEEKDAYS = [
  { value: 'Mon', label: 'Monday' },
  { value: 'Tue', label: 'Tuesday' },
  { value: 'Wed', label: 'Wednesday' },
  { value: 'Thu', label: 'Thursday' },
  { value: 'Fri', label: 'Friday' },
  { value: 'Sat', label: 'Saturday' },
  { value: 'Sun', label: 'Sunday' },
]

const TIMEZONES = [
  'UTC',
  'America/New_York',
  'America/Chicago',
  'America/Denver',
  'America/Los_Angeles',
  'Europe/London',
  'Europe/Berlin',
  'Europe/Paris',
  'Asia/Shanghai',
  'Asia/Tokyo',
  'Asia/Kolkata',
  'Australia/Sydney',
  'Pacific/Auckland',
]

interface EditDialogProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  schedule: ScheduleRecord
  onSave: (fields: ScheduleEditFields) => Promise<void>
  validationError?: string | null
  conflict?: boolean
  loading?: boolean
}

export function EditDialog({
  open,
  onOpenChange,
  schedule,
  onSave,
  validationError,
  conflict = false,
  loading = false,
}: EditDialogProps): JSX.Element {
  const isWeekly = schedule.kind === 'weekly'
  const initialRecurrence = schedule.recurrence ?? {}

  const [fireAtDate, setFireAtDate] = useState('')
  const [fireAtTime, setFireAtTime] = useState('')
  const [weekday, setWeekday] = useState(initialRecurrence.day ?? 'Mon')
  const [weeklyTime, setWeeklyTime] = useState(initialRecurrence.time ?? '09:00')
  const [timezone, setTimezone] = useState(schedule.timezone || 'UTC')
  const [localError, setLocalError] = useState<string | null>(null)

  const displayedError = localError ?? validationError

  useEffect(() => {
    setLocalError(null)
    if (!open) return
    const tz = schedule.timezone || 'UTC'

    if (!isWeekly && schedule.fire_at) {
      try {
        const d = new Date(schedule.fire_at)
        if (!Number.isNaN(d.getTime())) {
          const dateFmt = new Intl.DateTimeFormat('en-CA', {
            timeZone: tz,
            year: 'numeric',
            month: '2-digit',
            day: '2-digit',
          })
          const timeFmt = new Intl.DateTimeFormat('en-US', {
            timeZone: tz,
            hour: '2-digit',
            minute: '2-digit',
            hour12: false,
          })
          setFireAtDate(dateFmt.format(d))
          const timeParts = timeFmt.formatToParts(d)
          const hh = timeParts.find((p) => p.type === 'hour')?.value ?? '00'
          const mm = timeParts.find((p) => p.type === 'minute')?.value ?? '00'
          setFireAtTime(`${hh}:${mm}`)
        }
      } catch {
        // leave empty
      }
    }
    if (isWeekly) {
      setWeekday(initialRecurrence.day ?? 'Mon')
      setWeeklyTime(initialRecurrence.time ?? '09:00')
    }
    setTimezone(tz)
  }, [open, schedule, isWeekly, initialRecurrence.day, initialRecurrence.time])

  const nextFirePreview = useMemo((): { date: Date; tz: string } | null => {
    const tz = timezone || 'UTC'
    if (isWeekly) {
      const iso = nextWeeklyOccurrence(weekday, weeklyTime, tz)
      if (!iso) return null
      return { date: new Date(iso), tz }
    }
    if (fireAtDate && fireAtTime) {
      const iso = serializeOneShotInTz(fireAtDate, fireAtTime, tz)
      if (!iso) return null
      return { date: new Date(iso), tz }
    }
    return null
  }, [isWeekly, weekday, weeklyTime, fireAtDate, fireAtTime, timezone])

  const handleSave = async () => {
    setLocalError(null)
    const fields: ScheduleEditFields = {}

    if (isWeekly) {
      const tz = timezone || 'UTC'
      fields.recurrence = { day: weekday, time: weeklyTime, tz }
      fields.timezone = tz
      const fireAtIso = nextWeeklyOccurrence(weekday, weeklyTime, tz)
      if (!fireAtIso) {
        setLocalError(
          `This date and time does not exist in ${tz} (for example, during a daylight-saving transition).`,
        )
        return
      }
      fields.fire_at = fireAtIso
    } else {
      if (fireAtDate && fireAtTime) {
        const iso = serializeOneShotInTz(fireAtDate, fireAtTime, timezone || 'UTC')
        if (!iso) {
          setLocalError(
            `This date and time does not exist in ${timezone || 'UTC'} (for example, during a daylight-saving transition).`,
          )
          return
        }
        fields.fire_at = iso
      }
      if (timezone) fields.timezone = timezone
    }
    await onSave(fields)
  }

  if (conflict) {
    return (
      <Dialog open={open} onOpenChange={onOpenChange}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>This Todo was modified</DialogTitle>
            <DialogDescription>
              This Todo changed while you were editing it. Reload the page to
              see the current state before editing again.
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button
              onClick={() => {
                onOpenChange(false)
                window.location.reload()
              }}
            >
              Reload
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    )
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-md">
        <DialogHeader>
          <DialogTitle>Edit timing</DialogTitle>
          <DialogDescription>
            Change when this Todo fires. The original instruction and normalized
            commitment are not editable — only timing can change here.
          </DialogDescription>
        </DialogHeader>

        <div className="mt-4 space-y-4">
          <div className="space-y-1">
            <Label className="text-fg-subtle text-xs">Normalized commitment</Label>
            <p className="border-border-subtle bg-bg-subtle text-fg rounded border px-3 py-2 text-sm">
              {schedule.normalized_brief}
            </p>
          </div>
          <div className="space-y-1">
            <Label className="text-fg-subtle text-xs">Original instruction</Label>
            <p className="border-border-subtle bg-bg-subtle text-fg-muted rounded border px-3 py-2 text-sm italic">
              {schedule.source_instruction}
            </p>
          </div>

          {isWeekly ? (
            <>
              <div className="space-y-1">
                <Label htmlFor="edit-weekday">Weekday</Label>
                <Select value={weekday} onValueChange={setWeekday}>
                  <SelectTrigger id="edit-weekday">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    {WEEKDAYS.map((d) => (
                      <SelectItem key={d.value} value={d.value}>
                        {d.label}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>
              <div className="space-y-1">
                <Label htmlFor="edit-weekly-time">Time</Label>
                <Input
                  id="edit-weekly-time"
                  type="time"
                  value={weeklyTime}
                  onChange={(e) => setWeeklyTime(e.target.value)}
                />
              </div>
            </>
          ) : (
            <>
              <div className="space-y-1">
                <Label htmlFor="edit-fire-date">Date</Label>
                <Input
                  id="edit-fire-date"
                  type="date"
                  value={fireAtDate}
                  onChange={(e) => setFireAtDate(e.target.value)}
                />
              </div>
              <div className="space-y-1">
                <Label htmlFor="edit-fire-time">Time</Label>
                <Input
                  id="edit-fire-time"
                  type="time"
                  value={fireAtTime}
                  onChange={(e) => setFireAtTime(e.target.value)}
                />
              </div>
            </>
          )}

          <div className="space-y-1">
            <Label htmlFor="edit-timezone">Timezone</Label>
            <Select value={timezone} onValueChange={setTimezone}>
              <SelectTrigger id="edit-timezone">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {TIMEZONES.map((tz) => (
                  <SelectItem key={tz} value={tz}>
                    {tz}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>

          {nextFirePreview && (
            <div className="border-border-subtle bg-bg-subtle rounded border px-3 py-2">
              <span className="text-fg-subtle text-xs">
                Expected next fire · {nextFirePreview.tz}
              </span>
              <p className="text-fg text-sm font-semibold">
                {formatPreviewInTz(nextFirePreview.date, nextFirePreview.tz)}
              </p>
            </div>
          )}

          {displayedError && (
            <div className="border-tier-red bg-tier-red-tint text-tier-red rounded border px-3 py-2 text-sm">
              {displayedError}
            </div>
          )}
        </div>

        <DialogFooter>
          <Button
            variant="secondary"
            onClick={() => onOpenChange(false)}
            disabled={loading}
          >
            Cancel
          </Button>
          <Button onClick={handleSave} disabled={loading}>
            {loading ? 'Saving…' : 'Save changes'}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
