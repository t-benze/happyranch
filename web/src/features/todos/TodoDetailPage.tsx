/**
 * TodoDetailPage — detail view for a single Todo (schedule).
 * Routes at /orgs/:slug/todos/:scheduleId.
 *
 * Shows the schedule's full provenance: normalized commitment, source
 * instruction, owner, schedule details, spawned tasks, and audit link.
 *
 * Actions per the exact action matrix:
 *   Armed: Pause, Edit, Cancel
 *   Paused: Edit, Cancel
 *   All others: read-only (no actions)
 *
 * Data provenance: no fabricated failure cause, task, issuer, diagnostics,
 * or schedule data. Spawned task IDs link to real task-detail routes.
 * Activity link goes to filtered audit.
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
import type { ScheduleRecord, ScheduleEditFields } from '@/lib/api/types'

interface TodoDetailPageProps {
  scheduleId: string
  onBack: () => void
}

/** Format a UTC timestamp to a readable date. */
function fmtDate(iso: string): string {
  try {
    const d = new Date(iso)
    if (isNaN(d.getTime())) return iso
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

/** Describe the schedule concisely for the detail view. */
function describeSchedule(s: ScheduleRecord): string {
  if (s.kind === 'one_shot') {
    const d = s.fire_at ? new Date(s.fire_at) : null
    if (d && !isNaN(d.getTime())) {
      return `One-shot · fires on ${d.toLocaleDateString('en-US', {
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
    return 'One-shot schedule'
  }
  // weekly
  const day = s.recurrence?.day ?? '?'
  const time = s.recurrence?.time ?? '?'
  let line = `Weekly · every ${day} at ${time}`
  if (s.indefinite) line += ' · Indefinite'
  else if (s.expires_at) {
    const d = new Date(s.expires_at)
    if (!isNaN(d.getTime())) {
      line += ` · Review by ${d.toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' })}`
    }
  }
  return line
}

/** Fire-at display with timezone. */
function fireAtDisplay(s: ScheduleRecord): string {
  if (!s.fire_at) return 'Not scheduled'
  try {
    const d = new Date(s.fire_at)
    if (isNaN(d.getTime())) return s.fire_at
    return `${d.toLocaleDateString('en-US', {
      weekday: 'short',
      month: 'short',
      day: 'numeric',
      year: 'numeric',
      timeZone: 'UTC',
    })} · ${d.toLocaleTimeString('en-US', {
      hour: '2-digit',
      minute: '2-digit',
      timeZone: 'UTC',
    })}`
  } catch {
    return s.fire_at
  }
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
  if (s.status === 'failed' || s.status === 'timeout') {
    return null // No fabricated reason — show only the factual status
  }
  return null
}

export function TodoDetailPage({
  scheduleId,
  onBack,
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
          <div className="h-6 w-32 rounded bg-bg-subtle" />
          <div className="h-8 w-96 rounded bg-bg-subtle" />
          <div className="h-24 rounded-2xl bg-bg-subtle" />
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
      const status = (err as { status?: number })?.status
      const msg = (err as { message?: string })?.message ?? 'Edit failed.'
      if (status === 409) {
        setConflict(true)
      } else {
        setValidationError(msg)
      }
    }
  }

  return (
    <ContentWrap>
      {/* Back button */}
      <button
        type="button"
        onClick={onBack}
        className="inline-flex items-center gap-1.5 text-sm text-fg-muted hover:text-fg mb-4 transition-colors"
      >
        <ArrowLeft size={16} />
        All Todos
      </button>

      {/* Header: status pill + title + agent */}
      <div className="mb-6">
        <div className="flex items-center gap-3 mb-2">
          <StatusPill status={schedule.status} />
          <span className="text-sm text-fg-subtle font-mono">
            {schedule.schedule_id}
          </span>
        </div>
        <h1 className="text-h2 font-semibold text-fg leading-tight">
          {schedule.normalized_brief}
        </h1>
        <div className="flex items-center gap-1.5 mt-2 text-sm text-fg-muted">
          <span
            aria-hidden
            className="inline-flex size-4 shrink-0 items-center justify-center rounded bg-tier-green-tint text-xs font-semibold text-status-open"
          >
            {agentInitials(schedule.agent_name)}
          </span>
          <span>{schedule.agent_name}</span>
        </div>
      </div>

      <div className="flex flex-col gap-6 lg:flex-row">
        {/* Main column */}
        <div className="flex-1 min-w-0 space-y-5">
          {/* Schedule card */}
          <div className="rounded-2xl border border-border bg-bg-raised p-5 space-y-3">
            <h2 className="text-xs font-semibold text-fg-subtle uppercase tracking-wider">
              Schedule
            </h2>
            <p className="text-sm text-fg">{describeSchedule(schedule)}</p>
            {schedule.fire_at && (
              <div>
                <span className="text-xs text-fg-subtle uppercase tracking-wider">
                  {schedule.status === 'firing'
                    ? 'Firing'
                    : schedule.status === 'fired' || schedule.status === 'failed' || schedule.status === 'timeout'
                    ? 'Fired at'
                    : 'Next fire'}
                </span>
                <p className="text-sm font-semibold text-fg">
                  {fireAtDisplay(schedule)}
                  {schedule.timezone && (
                    <span className="ml-1 font-normal text-fg-muted">
                      {schedule.timezone}
                    </span>
                  )}
                </p>
              </div>
            )}
            {schedule.fire_count > 0 && (
              <p className="text-sm text-fg-muted">
                {schedule.fire_count} {schedule.fire_count === 1 ? 'run' : 'runs'}
                {schedule.last_fired_at && (
                  <span>
                    {' '}
                    · last fired {fmtDate(schedule.last_fired_at)}
                  </span>
                )}
              </p>
            )}
            {/* Recurrence review expiry callout */}
            {schedule.kind === 'weekly' && !schedule.indefinite && schedule.expires_at && schedule.status !== 'expired' && (
              <div className="rounded border border-attention-soft/50 bg-attention-soft/30 px-3 py-2 text-sm text-attention-text">
                <Clock size={14} className="inline-block mr-1.5 -mt-0.5" aria-hidden />
                Review due {new Date(schedule.expires_at).toLocaleDateString('en-US', {
                  month: 'short',
                  day: 'numeric',
                  year: 'numeric',
                })}. This Todo will stop firing after that date unless it was created as indefinite.
              </div>
            )}
            {schedule.indefinite && (
              <div className="rounded border border-border-subtle bg-bg-subtle px-3 py-2">
                <span className="text-xs text-fg-muted">Indefinite · no expiry</span>
              </div>
            )}
          </div>

          {/* Normalized commitment card */}
          <div className="rounded-2xl border border-border bg-bg-raised p-5 space-y-2">
            <h2 className="text-xs font-semibold text-fg-subtle uppercase tracking-wider">
              Normalized commitment
            </h2>
            <p className="text-sm text-fg">{schedule.normalized_brief}</p>
          </div>

          {/* Source instruction card */}
          <div className="rounded-2xl border border-border bg-bg-raised p-5 space-y-2">
            <h2 className="text-xs font-semibold text-fg-subtle uppercase tracking-wider">
              Original instruction
            </h2>
            <p className="text-sm text-fg-muted italic">
              {schedule.source_instruction}
            </p>
          </div>

          {/* Terminal explanation for fired one-shot */}
          {termExplanation && (
            <div className="rounded-2xl border border-border bg-bg-raised p-5">
              <p className="text-sm text-fg-muted">{termExplanation}</p>
            </div>
          )}

          {/* Spawned tasks */}
          {schedule.spawned_task_ids && schedule.spawned_task_ids.length > 0 && (
            <div className="rounded-2xl border border-border bg-bg-raised p-5 space-y-2">
              <h2 className="text-xs font-semibold text-fg-subtle uppercase tracking-wider">
                Spawned tasks
              </h2>
              <ul className="space-y-1">
                {schedule.spawned_task_ids.map((taskId) => (
                  <li key={taskId}>
                    <Link
                      to={`/orgs/${org}/tasks/${taskId}`}
                      className="inline-flex items-center gap-1 text-sm text-accent hover:underline font-mono"
                    >
                      {taskId}
                      <ExternalLink size={12} aria-hidden />
                    </Link>
                  </li>
                ))}
              </ul>
            </div>
          )}
        </div>

        {/* Right rail */}
        <div className="space-y-5">
          {/* Actions */}
          {actions !== 'readonly' && (
            <div className="rounded-2xl border border-border bg-bg-raised p-5 space-y-2">
              <h2 className="text-xs font-semibold text-fg-subtle uppercase tracking-wider mb-3">
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

          {/* Activity */}
          <div className="rounded-2xl border border-border bg-bg-raised p-5 space-y-2">
            <h2 className="text-xs font-semibold text-fg-subtle uppercase tracking-wider">
              Activity
            </h2>
            <Link
              to={`/orgs/${org}/audit?task_id=${schedule.schedule_id}`}
              className="inline-flex items-center gap-1 text-sm text-accent hover:underline"
            >
              View related activity
              <ExternalLink size={12} aria-hidden />
            </Link>
            {schedule.fire_count > 0 && (
              <p className="text-sm text-fg-muted">
                {schedule.fire_count} {schedule.fire_count === 1 ? 'run' : 'runs'} recorded
              </p>
            )}
          </div>

          {/* Record details */}
          <div className="rounded-2xl border border-border bg-bg-raised p-5 space-y-2">
            <h2 className="text-xs font-semibold text-fg-subtle uppercase tracking-wider mb-2">
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

      {/* Dialogs */}
      {schedule && (
        <>
          <ConfirmDialog
            open={pauseOpen}
            onOpenChange={setPauseOpen}
            title="Pause this Todo"
            description="This Todo will not fire while paused. You can resume it later from the paused list."
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
