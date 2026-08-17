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
  const isRecurring = schedule.kind === 'recurring'
  const initialRecurrence = schedule.recurrence ?? {}

  const [fireAtDate, setFireAtDate] = useState('')
  const [fireAtTime, setFireAtTime] = useState('')
  const [weekday, setWeekday] = useState(initialRecurrence.day ?? 'Mon')
  const [weeklyTime, setWeeklyTime] = useState(initialRecurrence.time ?? '09:00')
  const [timezone, setTimezone] = useState(schedule.timezone || 'UTC')
  const [frequency, setFrequency] = useState(String(initialRecurrence.freq ?? 'DAILY'))
  const [interval, setInterval] = useState(String(initialRecurrence.interval ?? 1))
  const [recurrenceTime, setRecurrenceTime] = useState(String(initialRecurrence.time ?? '09:00'))
  const [recurrenceDays, setRecurrenceDays] = useState<string[]>(
    Array.isArray(initialRecurrence.byday) ? initialRecurrence.byday : [],
  )
  const [monthMode, setMonthMode] = useState<'date' | 'ordinal'>(
    initialRecurrence.ordinal ? 'ordinal' : 'date',
  )
  const [monthDay, setMonthDay] = useState(String(initialRecurrence.bymonthday ?? 1))
  const [ordinal, setOrdinal] = useState(String(initialRecurrence.ordinal ?? 'first'))
  const [ordinalDay, setOrdinalDay] = useState(
    Array.isArray(initialRecurrence.byday) ? String(initialRecurrence.byday[0] ?? 'MO') : 'MO',
  )
  const [ends, setEnds] = useState<'never' | 'on' | 'after'>(
    initialRecurrence.until ? 'on' : initialRecurrence.count ? 'after' : 'never',
  )
  const [until, setUntil] = useState(String(initialRecurrence.until ?? ''))
  const [count, setCount] = useState(String(initialRecurrence.count ?? 1))
  // Blank by default: merely opening and saving an existing recurrence must
  // never change its server-owned cadence phase.
  const [startDate, setStartDate] = useState('')
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
    if (isRecurring) {
      setStartDate('')
      setFrequency(String(initialRecurrence.freq ?? 'DAILY'))
      setInterval(String(initialRecurrence.interval ?? 1))
      setRecurrenceTime(String(initialRecurrence.time ?? '09:00'))
      setRecurrenceDays(Array.isArray(initialRecurrence.byday) ? initialRecurrence.byday : [])
      setMonthMode(initialRecurrence.ordinal ? 'ordinal' : 'date')
      setMonthDay(String(initialRecurrence.bymonthday ?? 1))
      setOrdinal(String(initialRecurrence.ordinal ?? 'first'))
      setOrdinalDay(Array.isArray(initialRecurrence.byday) ? String(initialRecurrence.byday[0] ?? 'MO') : 'MO')
      setEnds(initialRecurrence.until ? 'on' : initialRecurrence.count ? 'after' : 'never')
      setUntil(String(initialRecurrence.until ?? ''))
      setCount(String(initialRecurrence.count ?? 1))
    }
    setTimezone(tz)
  }, [
    open, schedule, isWeekly, isRecurring, initialRecurrence.byday,
    initialRecurrence.bymonthday, initialRecurrence.count, initialRecurrence.day,
    initialRecurrence.freq, initialRecurrence.interval, initialRecurrence.ordinal,
    initialRecurrence.time, initialRecurrence.until,
  ])

  const nextFirePreview = useMemo((): { date: Date; tz: string } | null => {
    const tz = timezone || 'UTC'
    if (isWeekly) {
      const iso = nextWeeklyOccurrence(weekday, weeklyTime, tz)
      if (!iso) return null
      return { date: new Date(iso), tz }
    }
    if (isRecurring) return null
    if (fireAtDate && fireAtTime) {
      const iso = serializeOneShotInTz(fireAtDate, fireAtTime, tz)
      if (!iso) return null
      return { date: new Date(iso), tz }
    }
    return null
  }, [isWeekly, isRecurring, weekday, weeklyTime, fireAtDate, fireAtTime, timezone])

  const handleSave = async () => {
    setLocalError(null)
    const fields: ScheduleEditFields = {}

    if (isRecurring) {
      const parsedInterval = Number(interval)
      if (!Number.isInteger(parsedInterval) || parsedInterval < 1) {
        setLocalError('Repeat interval must be a positive whole number.')
        return
      }
      if (frequency === 'WEEKLY' && recurrenceDays.length === 0) {
        setLocalError('Choose at least one weekday for a weekly recurrence.')
        return
      }
      if (frequency === 'MONTHLY' && monthMode === 'date' && (!Number.isInteger(Number(monthDay)) || Number(monthDay) < 1 || Number(monthDay) > 31)) {
        setLocalError('Choose a calendar date from 1 through 31.')
        return
      }
      if (ends === 'on' && !until) {
        setLocalError('Choose an end date.')
        return
      }
      if (ends === 'after' && (!Number.isInteger(Number(count)) || Number(count) < 1)) {
        setLocalError('Occurrence count must be a positive whole number.')
        return
      }
      const recurrence: Record<string, string | number | string[] | null> = {
        freq: frequency,
        interval: parsedInterval,
        time: recurrenceTime,
        tz: timezone || 'UTC',
        until: ends === 'on' ? until : null,
        count: ends === 'after' ? Number(count) : null,
        byday: null,
        bymonthday: null,
        ordinal: null,
      }
      if (frequency === 'WEEKLY') recurrence.byday = recurrenceDays
      if (frequency === 'MONTHLY' && monthMode === 'date') recurrence.bymonthday = Number(monthDay)
      if (frequency === 'MONTHLY' && monthMode === 'ordinal') {
        recurrence.ordinal = ordinal
        recurrence.byday = [ordinalDay]
      }
      fields.recurrence = recurrence
      fields.timezone = timezone || 'UTC'
      if (startDate) fields.start_date = startDate
    } else if (isWeekly) {
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

          {isRecurring ? (
            <>
              <RecurringFields
                frequency={frequency} setFrequency={setFrequency}
                interval={interval} setInterval={setInterval}
                time={recurrenceTime} setTime={setRecurrenceTime}
                days={recurrenceDays} setDays={setRecurrenceDays}
                monthMode={monthMode} setMonthMode={setMonthMode}
                monthDay={monthDay} setMonthDay={setMonthDay}
                ordinal={ordinal} setOrdinal={setOrdinal}
                ordinalDay={ordinalDay} setOrdinalDay={setOrdinalDay}
                ends={ends} setEnds={setEnds} until={until} setUntil={setUntil}
                count={count} setCount={setCount}
              />
              <div className="space-y-1">
              <Label htmlFor="edit-recurrence-start-date">Rephase starting on (optional)</Label>
              <Input
                id="edit-recurrence-start-date"
                aria-describedby="edit-recurrence-start-date-help"
                type="date"
                value={startDate}
                onChange={(event) => setStartDate(event.target.value)}
              />
              <p id="edit-recurrence-start-date-help" className="text-fg-muted text-xs">
                Leave blank to preserve this Todo’s current phase. When set, the server validates the date and derives the first fire.
              </p>
              </div>
            </>
          ) : isWeekly ? (
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

interface RecurringFieldsProps {
  frequency: string; setFrequency: (value: string) => void; interval: string; setInterval: (value: string) => void
  time: string; setTime: (value: string) => void; days: string[]; setDays: (value: string[]) => void
  monthMode: 'date' | 'ordinal'; setMonthMode: (value: 'date' | 'ordinal') => void
  monthDay: string; setMonthDay: (value: string) => void; ordinal: string; setOrdinal: (value: string) => void
  ordinalDay: string; setOrdinalDay: (value: string) => void; ends: 'never' | 'on' | 'after'; setEnds: (value: 'never' | 'on' | 'after') => void
  until: string; setUntil: (value: string) => void; count: string; setCount: (value: string) => void
}

function RecurringFields(props: RecurringFieldsProps): JSX.Element {
  const toggleDay = (day: string) => props.setDays(props.days.includes(day) ? props.days.filter((d) => d !== day) : [...props.days, day])
  return <>
    <div className="grid grid-cols-2 gap-3">
      <div className="space-y-1"><Label htmlFor="edit-recurrence-interval">Repeat every</Label><Input id="edit-recurrence-interval" type="number" min="1" value={props.interval} onChange={(e) => props.setInterval(e.target.value)} /></div>
      <div className="space-y-1"><Label htmlFor="edit-recurrence-frequency">Frequency</Label><Select value={props.frequency} onValueChange={props.setFrequency}><SelectTrigger id="edit-recurrence-frequency"><SelectValue /></SelectTrigger><SelectContent>{[['DAILY','day'],['WEEKLY','week'],['MONTHLY','month'],['YEARLY','year']].map(([value,label]) => <SelectItem key={value} value={value}>{label}</SelectItem>)}</SelectContent></Select></div>
    </div>
    {props.frequency === 'WEEKLY' && <fieldset className="space-y-2"><legend className="text-sm font-medium">Repeat on</legend><div className="flex flex-wrap gap-2">{WEEKDAYS.map((day) => <label key={day.value} className="text-fg flex items-center gap-1 text-sm"><input type="checkbox" checked={props.days.includes(day.value.toUpperCase().slice(0, 2))} onChange={() => toggleDay(day.value.toUpperCase().slice(0, 2))} />{day.label}</label>)}</div></fieldset>}
    {props.frequency === 'MONTHLY' && <fieldset className="space-y-2"><legend className="text-sm font-medium">Monthly pattern</legend><div className="flex gap-4 text-sm"><label><input type="radio" name="monthly-mode" checked={props.monthMode === 'date'} onChange={() => props.setMonthMode('date')} /> Calendar date</label><label><input type="radio" name="monthly-mode" checked={props.monthMode === 'ordinal'} onChange={() => props.setMonthMode('ordinal')} /> Named weekday</label></div>{props.monthMode === 'date' ? <div className="space-y-1"><Label htmlFor="edit-month-day">Date</Label><Input id="edit-month-day" type="number" min="1" max="31" value={props.monthDay} onChange={(e) => props.setMonthDay(e.target.value)} /></div> : <div className="grid grid-cols-2 gap-3"><div className="space-y-1"><Label htmlFor="edit-month-ordinal">Ordinal</Label><Select value={props.ordinal} onValueChange={props.setOrdinal}><SelectTrigger id="edit-month-ordinal"><SelectValue /></SelectTrigger><SelectContent>{['first','second','third','fourth','fifth','last'].map((value) => <SelectItem key={value} value={value}>{value}</SelectItem>)}</SelectContent></Select></div><div className="space-y-1"><Label htmlFor="edit-month-weekday">Weekday</Label><Select value={props.ordinalDay} onValueChange={props.setOrdinalDay}><SelectTrigger id="edit-month-weekday"><SelectValue /></SelectTrigger><SelectContent>{WEEKDAYS.map((day) => <SelectItem key={day.value} value={day.value.toUpperCase().slice(0,2)}>{day.label}</SelectItem>)}</SelectContent></Select></div></div>}</fieldset>}
    <div className="space-y-1"><Label htmlFor="edit-recurrence-time">Time</Label><Input id="edit-recurrence-time" type="time" value={props.time} onChange={(e) => props.setTime(e.target.value)} /></div>
    <fieldset className="space-y-2"><legend className="text-sm font-medium">Ends</legend><div className="flex flex-wrap gap-3 text-sm"><label><input type="radio" name="recurrence-ends" checked={props.ends === 'never'} onChange={() => props.setEnds('never')} /> Never</label><label><input type="radio" name="recurrence-ends" checked={props.ends === 'on'} onChange={() => props.setEnds('on')} /> On date</label><label><input type="radio" name="recurrence-ends" checked={props.ends === 'after'} onChange={() => props.setEnds('after')} /> After count</label></div>{props.ends === 'on' && <Input aria-label="End date" type="date" value={props.until} onChange={(e) => props.setUntil(e.target.value)} />}{props.ends === 'after' && <Input aria-label="Occurrence count" type="number" min="1" value={props.count} onChange={(e) => props.setCount(e.target.value)} />}</fieldset>
  </>
}
