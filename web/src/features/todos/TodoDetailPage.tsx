/**
 * TodoDetailPage — detail view for a single Todo (schedule).
 *
 * Routes at /orgs/:slug/todos/:scheduleId.
 */
import { useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import { ArrowLeft, Clock, ExternalLink } from 'lucide-react'
import { ContentWrap } from '@/design-system/layouts/ContentWrap/ContentWrap'
import { Button } from '@/design-system/primitives/Button'
import { useTodoDetail, usePauseTodo, useCancelTodo, useEditTodo } from './hooks'
import { StatusPill } from './components/StatusPill'
import { ConfirmDialog } from './components/ConfirmDialog'
import { EditDialog } from './components/EditDialog'
import { formatFireAtInTz } from './timezone'
import type { ScheduleRecord, ScheduleEditFields } from '@/lib/api/types'

interface TodoDetailPageProps {
  scheduleId: string
}

/** Format a UTC timestamp to a readable date (UTC metadata only). */
function fmtDate(iso: string): string {
  try {
    const d = new Date(iso)
    if (Number.isNaN(d.getTime())) return iso
    return d.toLocaleDateString('en-US', {
      month: 'short',
      day: 'numeric',
      year: 'numeric',
      hour: '2-digit',
      minute: '2-digit',
      timeZone: 'UTC',
    })
  } catch {
    return iso
  }
}

/** Initials from agent name. */
function agentInitials(name: string): string {
  return name
    .split(/[_\-.]+/)
    .slice(0, 2)
    .map((p) => p[0]?.toUpperCase() ?? '')
    .join('')
}

/** Describe the schedule concisely in the stored IANA timezone. */
function describeSchedule(s: ScheduleRecord): string {
  const tz = s.timezone || 'UTC'
  if (s.kind === 'one_shot') {
    if (s.fire_at) {
      const formatted = formatFireAtInTz(s.fire_at, tz)
      if (formatted !== s.fire_at) return `One-shot · fires on ${formatted}`
    }
    return 'One-shot schedule'
  }
  const day = s.recurrence?.day ?? '?'
  const time = s.recurrence?.time ?? '?'
  let line = `Weekly · every ${day} at ${time}`
  if (s.indefinite) line += ` · Indefinite`
  else if (s.expires_at) {
    const d = new Date(s.expires_at)
    if (!Number.isNaN(d.getTime())) {
      line += ` · Review by ${d.toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' })}`
    }
  }
  return line
}

/** Fire-at display in the stored IANA timezone. */
function fireAtDisplay(s: ScheduleRecord): string {
  if (!s.fire_at) return 'Not scheduled'
  const tz = s.timezone || 'UTC'
  return formatFireAtInTz(s.fire_at, tz)
}

/** Derive the actions available for a given status. */
function permittedActions(status: string): 'armed' | 'paused' | 'readonly' {
  if (status === 'armed') return 'armed'
  if (status === 'paused') return 'paused'
  return 'readonly'
}

/** Fired one-shot explanation. */
function terminalExplanation(s: ScheduleRecord): string | null {
  if (s.kind === 'one_shot' && s.status === 'fired') {
    return 'This Todo fired once. See the linked task for the work outcome.'
  }
  return null
}

export function TodoDetailPage({
  scheduleId,
}: TodoDetailPageProps): JSX.Element {
  const { slug } = useParams<{ slug: string }>()
  const org = slug ?? ''

  const { data: schedule, isLoading, isError, refetch } = useTodoDetail(org, scheduleId)
  const pauseMutation = usePauseTodo(org)
  const cancelMutation = useCancelTodo(org)
  const editMutation = useEditTodo(org)

  const [pauseOpen, setPauseOpen] = useState(false)
  const [cancelOpen, setCancelOpen] = useState(false)
  const [editOpen, setEditOpen] = useState(false)
  const [validationError, setValidationError] = useState<string | null>(null)
  const [conflict, setConflict] = useState(false)

  if (isLoading) {
    return (
      <ContentWrap>
        <div className="animate-pulse space-y-4">
          <div className="bg-bg-subtle h-6 w-32 rounded" />
          <div className="bg-bg-subtle h-8 w-96 rounded" />
          <div className="bg-bg-subtle h-24 rounded-lg" />
        </div>
      </ContentWrap>
    )
  }

  if (isError || !schedule) {
    return (
      <ContentWrap>
        <div className="flex flex-col items-center py-16 text-center">
          <p className="text-fg-muted mb-4">Failed to load this Todo.</p>
          <Button onClick={() => refetch()}>Retry</Button>
        </div>
      </ContentWrap>
    )
  }

  const actions = permittedActions(schedule.status)
  const termExplanation = terminalExplanation(schedule)

  const handlePause = async () => {
    try {
      await pauseMutation.mutateAsync(scheduleId)
      setPauseOpen(false)
    } catch {
      // stays open — user sees error via mutation state
    }
  }

  const handleCancel = async () => {
    try {
      await cancelMutation.mutateAsync(scheduleId)
      setCancelOpen(false)
    } catch {
      // stays open
    }
  }

  const handleEditSave = async (fields: ScheduleEditFields) => {
    setValidationError(null)
    setConflict(false)
    try {
      await editMutation.mutateAsync({ scheduleId, fields })
      setEditOpen(false)
    } catch (err: unknown) {
      const apiErr = err as { status?: number; message?: string; detail?: unknown }
      const status = apiErr.status
      const msg =
        typeof apiErr.detail === 'string'
          ? apiErr.detail
          : (apiErr.message ?? 'Edit failed.')
      if (status === 409) {
        setConflict(true)
      } else {
        setValidationError(msg)
      }
    }
  }

  const tz = schedule.timezone || 'UTC'

  return (
    <ContentWrap>
      <Link
        to={`/orgs/${org}/todos`}
        className="text-fg-muted hover:text-fg mb-4 inline-flex items-center gap-1.5 text-sm transition-colors"
      >
        <ArrowLeft size={16} />
        All Todos
      </Link>

      <div className="mb-6">
        <div className="mb-2 flex items-center gap-3">
          <StatusPill status={schedule.status} />
          <span className="text-fg-subtle font-mono text-sm">
            {schedule.schedule_id}
          </span>
        </div>
        <h1 className="text-h2 text-fg leading-tight font-semibold">
          {schedule.normalized_brief}
        </h1>
        <div className="text-fg-muted mt-2 flex items-center gap-1.5 text-sm">
          <span
            aria-hidden="true"
            className="bg-tier-green-tint text-status-open inline-flex size-4 shrink-0 items-center justify-center rounded text-xs font-semibold"
          >
            {agentInitials(schedule.agent_name)}
          </span>
          <span>{schedule.agent_name}</span>
        </div>
      </div>

      <div className="flex flex-col gap-6 lg:flex-row">
        <div className="min-w-0 flex-1 space-y-5">
          <div className="border-border bg-bg-raised space-y-3 rounded-lg border p-5">
            <h2 className="text-fg-subtle text-xs font-semibold tracking-wider uppercase">
              Schedule
            </h2>
            <p className="text-fg text-sm">{describeSchedule(schedule)}</p>
            {schedule.fire_at && (
              <div>
                <span className="text-fg-subtle text-xs tracking-wider uppercase">
                  {schedule.status === 'firing'
                    ? 'Firing'
                    : schedule.status === 'fired' ||
                      schedule.status === 'failed' ||
                      schedule.status === 'timeout'
                    ? 'Fired at'
                    : 'Next fire'}
                </span>
                <p className="text-fg text-sm font-semibold">
                  {fireAtDisplay(schedule)}
                  {tz && <span className="text-fg-muted ml-1 font-normal">{tz}</span>}
                </p>
              </div>
            )}
            {schedule.fire_count > 0 && (
              <p className="text-fg-muted text-sm">
                {schedule.fire_count} {schedule.fire_count === 1 ? 'run' : 'runs'}
                {schedule.last_fired_at && (
                  <span> · last fired {fmtDate(schedule.last_fired_at)}</span>
                )}
              </p>
            )}
            {schedule.kind === 'weekly' &&
              !schedule.indefinite &&
              schedule.expires_at &&
              schedule.status !== 'expired' && (
                <div className="border-attention-soft bg-attention-soft/30 text-attention-text rounded border px-3 py-2 text-sm">
                  <Clock
                    size={14}
                    className="-mt-0.5 mr-1.5 inline-block"
                    aria-hidden="true"
                  />
                  Review due{' '}
                  {new Date(schedule.expires_at).toLocaleDateString('en-US', {
                    month: 'short',
                    day: 'numeric',
                    year: 'numeric',
                  })}.
                  This Todo will stop firing after that date unless it was created as
                  indefinite.
                </div>
              )}
            {Boolean(schedule.indefinite) && (
              <div className="border-border-subtle bg-bg-subtle rounded border px-3 py-2">
                <span className="text-fg-muted text-xs">Indefinite · no expiry</span>
              </div>
            )}
          </div>

          <div className="border-border bg-bg-raised space-y-2 rounded-lg border p-5">
            <h2 className="text-fg-subtle text-xs font-semibold tracking-wider uppercase">
              Normalized commitment
            </h2>
            <p className="text-fg text-sm">{schedule.normalized_brief}</p>
          </div>

          <div className="border-border bg-bg-raised space-y-2 rounded-lg border p-5">
            <h2 className="text-fg-subtle text-xs font-semibold tracking-wider uppercase">
              Original instruction
            </h2>
            <p className="text-fg-muted text-sm italic">
              {schedule.source_instruction}
            </p>
          </div>

          {termExplanation && (
            <div className="border-border bg-bg-raised rounded-lg border p-5">
              <p className="text-fg-muted text-sm">{termExplanation}</p>
            </div>
          )}

          {schedule.spawned_task_ids && schedule.spawned_task_ids.length > 0 && (
            <div className="border-border bg-bg-raised space-y-2 rounded-lg border p-5">
              <h2 className="text-fg-subtle text-xs font-semibold tracking-wider uppercase">
                Spawned tasks
              </h2>
              <ul className="space-y-1">
                {schedule.spawned_task_ids.map((taskId) => (
                  <li key={taskId}>
                    <Link
                      to={`/orgs/${org}/tasks/${taskId}`}
                      className="text-accent inline-flex items-center gap-1 font-mono text-sm hover:underline"
                    >
                      {taskId}
                      <ExternalLink size={12} aria-hidden="true" />
                    </Link>
                  </li>
                ))}
              </ul>
            </div>
          )}
        </div>

        <div className="space-y-5">
          {actions !== 'readonly' && (
            <div className="border-border bg-bg-raised space-y-2 rounded-lg border p-5">
              <h2 className="text-fg-subtle mb-3 text-xs font-semibold tracking-wider uppercase">
                Actions
              </h2>
              {actions === 'armed' && (
                <Button
                  variant="secondary"
                  className="w-full"
                  onClick={() => setPauseOpen(true)}
                >
                  Pause
                </Button>
              )}
              <Button
                variant="secondary"
                className="w-full"
                onClick={() => setEditOpen(true)}
              >
                Edit timing
              </Button>
              <Button
                variant="destructive"
                className="w-full"
                onClick={() => setCancelOpen(true)}
              >
                Cancel
              </Button>
            </div>
          )}

          <div className="border-border bg-bg-raised space-y-2 rounded-lg border p-5">
            <h2 className="text-fg-subtle text-xs font-semibold tracking-wider uppercase">
              Activity
            </h2>
            <Link
              to={`/orgs/${org}/audit?task_id=${schedule.schedule_id}`}
              className="text-accent inline-flex items-center gap-1 text-sm hover:underline"
            >
              View related activity
              <ExternalLink size={12} aria-hidden="true" />
            </Link>
            {schedule.fire_count > 0 && (
              <p className="text-fg-muted text-sm">
                {schedule.fire_count} {schedule.fire_count === 1 ? 'run' : 'runs'} recorded
              </p>
            )}
          </div>

          <div className="border-border bg-bg-raised space-y-2 rounded-lg border p-5">
            <h2 className="text-fg-subtle mb-2 text-xs font-semibold tracking-wider uppercase">
              Record details
            </h2>
            <dl className="space-y-2 text-xs">
              <div className="flex justify-between gap-2">
                <dt className="text-fg-subtle">Schedule ID</dt>
                <dd className="text-fg-muted font-mono">{schedule.schedule_id}</dd>
              </div>
              <div className="flex justify-between gap-2">
                <dt className="text-fg-subtle">Team</dt>
                <dd className="text-fg-muted">{schedule.team}</dd>
              </div>
              <div className="flex justify-between gap-2">
                <dt className="text-fg-subtle">Created</dt>
                <dd className="text-fg-muted">{fmtDate(schedule.created_at)}</dd>
              </div>
              <div className="flex justify-between gap-2">
                <dt className="text-fg-subtle">Updated</dt>
                <dd className="text-fg-muted">{fmtDate(schedule.updated_at)}</dd>
              </div>
            </dl>
          </div>
        </div>
      </div>

      {schedule && (
        <>
          <ConfirmDialog
            open={pauseOpen}
            onOpenChange={setPauseOpen}
            title="Pause this Todo"
            description="This Todo will not fire while paused."
            confirmLabel="Pause"
            confirmVariant="default"
            loading={pauseMutation.isPending}
            onConfirm={handlePause}
          />

          <ConfirmDialog
            open={cancelOpen}
            onOpenChange={setCancelOpen}
            title="Cancel this Todo"
            description="Once cancelled, this Todo cannot be re-activated. The agent will not receive any tasks from it."
            confirmLabel="Cancel Todo"
            confirmVariant="destructive"
            loading={cancelMutation.isPending}
            onConfirm={handleCancel}
          />

          <EditDialog
            open={editOpen}
            onOpenChange={(open) => {
              setEditOpen(open)
              if (!open) {
                setValidationError(null)
                setConflict(false)
              }
            }}
            schedule={schedule}
            onSave={handleEditSave}
            validationError={validationError}
            conflict={conflict}
            onDismissConflict={() => {
              refetch()
              setConflict(false)
            }}
            loading={editMutation.isPending}
          />
        </>
      )}
    </ContentWrap>
  )
}
