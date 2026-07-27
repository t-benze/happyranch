/**
 * EditDialog — edit a schedule's timing (fire_at for one-shot, or
 * weekday + time + timezone for weekly). Source instruction and
 * normalized brief remain read-only.
 *
 * States: normal edit, validation rejection (shows error inline), 409
 * conflict (explicit reload prompt).
 */
import { useState, useEffect, useMemo } from 'react'
import {
  Dialog,
  DialogContent,
  DialogDescription,
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

const WEEKDAYS = [
  { value: 'Mon', label: 'Monday' },
  { value: 'Tue', label: 'Tuesday' },
  { value: 'Wed', label: 'Wednesday' },
  { value: 'Thu', label: 'Thursday' },
  { value: 'Fri', label: 'Friday' },
  { value: 'Sat', label: 'Saturday' },
  { value: 'Sun', label: 'Sunday' },
]

/** Common timezones — display-only list; the actual value is from ScheduleRecord.timezone. */
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
  /** Called with the edit fields when the user confirms. */
  onSave: (fields: ScheduleEditFields) => Promise<void>
  /** Server-side validation error to display in the dialog. */
  validationError?: string | null
  /** 409 conflict — when true, the dialog shows a reload prompt instead of the form. */
  conflict?: boolean
  /** Reset conflict state so the user can dismiss. */
  onDismissConflict?: () => void
  loading?: boolean
}

export function EditDialog({
  open,
  onOpenChange,
  schedule,
  onSave,
  validationError,
  conflict = false,
  onDismissConflict,
  loading = false,
}: EditDialogProps): JSX.Element {
  const isWeekly = schedule.kind === 'weekly'
  const initialRecurrence = schedule.recurrence ?? {}

  // Editable fields — derived from the current schedule record
  const [fireAtDate, setFireAtDate] = useState('')
  const [fireAtTime, setFireAtTime] = useState('')
  const [weekday, setWeekday] = useState(initialRecurrence.day ?? 'Mon')
  const [weeklyTime, setWeeklyTime] = useState(initialRecurrence.time ?? '09:00')
  const [timezone, setTimezone] = useState(schedule.timezone || 'UTC')

  // Reset form state when the schedule or open state changes
  useEffect(() => {
    if (!open) return
    // Parse fire_at into date+time parts for one-shot
    if (!isWeekly && schedule.fire_at) {
      try {
        const d = new Date(schedule.fire_at)
        if (!isNaN(d.getTime())) {
          setFireAtDate(d.toISOString().slice(0, 10))
          setFireAtTime(
            d.toTimeString().slice(0, 5)
          )
        }
      } catch {
        // leave empty
      }
    }
    if (isWeekly) {
      setWeekday(initialRecurrence.day ?? 'Mon')
      setWeeklyTime(initialRecurrence.time ?? '09:00')
    }
    setTimezone(schedule.timezone || 'UTC')
  }, [open, schedule, isWeekly, initialRecurrence.day, initialRecurrence.time])

  // Preview next fire for display (naive, local-only for visual feedback)
  const nextFirePreview = useMemo(() => {
    if (isWeekly) {
      const days = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat']
      const target = days.indexOf(weekday)
      if (target < 0) return null
      const now = new Date()
      const [h, m] = weeklyTime.split(':').map(Number)
      const d = new Date(now)
      d.setDate(d.getDate() + ((target + 7 - d.getDay()) % 7 || 7))
      d.setHours(h || 9, m || 0, 0, 0)
      if (d <= now) d.setDate(d.getDate() + 7)
      return d
    }
    if (fireAtDate && fireAtTime) {
      const d = new Date(`${fireAtDate}T${fireAtTime}:00`)
      return isNaN(d.getTime()) ? null : d
    }
    return null
  }, [isWeekly, weekday, weeklyTime, fireAtDate, fireAtTime])

  const handleSave = async () => {
    const fields: ScheduleEditFields = {}
    if (isWeekly) {
      fields.recurrence = { day: weekday, time: weeklyTime }
      if (timezone) fields.timezone = timezone
    } else {
      if (fireAtDate && fireAtTime) {
        fields.fire_at = `${fireAtDate}T${fireAtTime}:00`
      }
      if (timezone) fields.timezone = timezone
    }
    await onSave(fields)
  }

  // 409 conflict state — explicit reload prompt
  if (conflict) {
    return (
      <Dialog open={open} onOpenChange={onOpenChange}>
        <DialogContent>
          <DialogTitle>This Todo was modified</DialogTitle>
          <DialogDescription>
            This Todo changed while you were editing it — most likely it fired.
            The page will reload the current record so you can see the actual
            state.
          </DialogDescription>
          <div className="mt-5 flex justify-end gap-3">
            <Button variant="secondary" onClick={onDismissConflict ?? (() => onOpenChange(false))}>
              Dismiss
            </Button>
            <Button onClick={() => { onOpenChange(false); window.location.reload() }}>
              Reload record
            </Button>
          </div>
        </DialogContent>
      </Dialog>
    )
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-md">
        <DialogTitle>Edit timing</DialogTitle>
        <DialogDescription>
          Change when this Todo fires. The original instruction and normalized
          commitment are not editable — only timing can change here. The
          schedule will be re-validated after saving.
        </DialogDescription>

        <div className="mt-4 space-y-4">
          {/* Read-only context */}
          <div className="space-y-1">
            <Label className="text-xs text-fg-subtle">Normalized commitment</Label>
            <p className="text-sm text-fg rounded border border-border-subtle bg-bg-subtle px-3 py-2">
              {schedule.normalized_brief}
            </p>
          </div>
          <div className="space-y-1">
            <Label className="text-xs text-fg-subtle">Original instruction</Label>
            <p className="text-sm text-fg-muted rounded border border-border-subtle bg-bg-subtle px-3 py-2 italic">
              {schedule.source_instruction}
            </p>
          </div>

          {/* Editable timing fields */}
          {isWeekly ? (
            <>
              <div className="space-y-1">
                <Label>Weekday</Label>
                <Select value={weekday} onValueChange={setWeekday}>
                  <SelectTrigger>
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
                <Label>Time</Label>
                <Input
                  type="time"
                  value={weeklyTime}
                  onChange={(e) => setWeeklyTime(e.target.value)}
                />
              </div>
            </>
          ) : (
            <>
              <div className="space-y-1">
                <Label>Date</Label>
                <Input
                  type="date"
                  value={fireAtDate}
                  onChange={(e) => setFireAtDate(e.target.value)}
                />
              </div>
              <div className="space-y-1">
                <Label>Time</Label>
                <Input
                  type="time"
                  value={fireAtTime}
                  onChange={(e) => setFireAtTime(e.target.value)}
                />
              </div>
            </>
          )}

          <div className="space-y-1">
            <Label>Timezone</Label>
            <Select value={timezone} onValueChange={setTimezone}>
              <SelectTrigger>
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

          {/* Preview next fire */}
          {nextFirePreview && (
            <div className="rounded border border-border-subtle bg-bg-subtle px-3 py-2">
              <span className="text-xs text-fg-subtle">Expected next fire · {timezone}</span>
              <p className="text-sm font-semibold text-fg">
                {nextFirePreview.toLocaleDateString('en-US', {
                  weekday: 'short',
                  month: 'short',
                  day: 'numeric',
                  year: 'numeric',
                })}{' '}
                ·{' '}
                {nextFirePreview.toLocaleTimeString('en-US', {
                  hour: '2-digit',
                  minute: '2-digit',
                })}
              </p>
            </div>
          )}

          {/* Validation error */}
          {validationError && (
            <div className="rounded border border-status-escalated/30 bg-danger-soft px-3 py-2 text-sm text-feedback-danger">
              {validationError}
            </div>
          )}
        </div>

        <div className="mt-5 flex justify-end gap-3">
          <Button variant="secondary" onClick={() => onOpenChange(false)} disabled={loading}>
            Cancel
          </Button>
          <Button onClick={handleSave} disabled={loading}>
            {loading ? 'Saving…' : 'Save changes'}
          </Button>
        </div>
      </DialogContent>
    </Dialog>
  )
}
