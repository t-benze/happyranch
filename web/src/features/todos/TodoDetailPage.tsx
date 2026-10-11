import { IdentityName } from '@/shared/identities/IdentityName';
/**
 * TodoDetailPage — detail view for a single Todo (schedule).
 *
 * Routes at /orgs/:slug/todos/:scheduleId.
 */
import { useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import { ArrowLeft, Clock, ExternalLink, Pause, Pencil, X } from 'lucide-react'
import { ContentWrap } from '@/design-system/layouts/ContentWrap/ContentWrap'
import { Button } from '@/design-system/primitives/Button'
import { useTodoDetail, usePauseTodo, useCancelTodo, useEditTodo } from './hooks'
import { StatusPill } from './components/StatusPill'
import { ConfirmDialog } from './components/ConfirmDialog'
import { EditDialog } from './components/EditDialog'
import { formatFireAtInTz, formatReviewDate } from './timezone'
import { formatRecurringRule } from './recurrence'
import {
  classifyTodoError,
  shortDayLabel,
  type TodoErrorView,
  type Translate,
} from './strings'
import { useTranslation } from '@/hooks/i18n'
import type { Locale, MessageKey } from '@/lib/i18n'
import { formatCountFor, formatDateTimeFor } from '@/lib/i18n/format'
import type { ScheduleRecord, ScheduleEditFields } from '@/lib/api/types'

interface TodoDetailPageProps {
  scheduleId: string
}

/** Format a UTC timestamp to a readable date (UTC metadata only). */
function fmtDate(iso: string, locale: Locale): string {
  try {
    const d = new Date(iso)
    if (Number.isNaN(d.getTime())) return iso
    return formatDateTimeFor(locale, d, { timeZone: 'UTC' })
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

/** Describe the schedule concisely in the stored IANA timezone, status-aware. */
function describeSchedule(s: ScheduleRecord, t: Translate, locale: Locale): string {
  const tz = s.timezone || 'UTC'
  const isPast = ['fired', 'failed', 'timeout', 'expired', 'cancelled'].includes(s.status)

  if (s.kind === 'one_shot') {
    if (s.fire_at) {
      const formatted = formatFireAtInTz(s.fire_at, tz, locale)
      if (formatted !== s.fire_at) return t('todos.schedule.onceOn', { when: formatted })
    }
    return t('todos.schedule.oneShotSchedule')
  }

  if (s.kind === 'recurring') return formatRecurringRule(s.recurrence, tz, t)

  const day = shortDayLabel(String(s.recurrence?.day ?? '?'), t)
  const time = String(s.recurrence?.time ?? '?')
  let line = t(isPast ? 'todos.schedule.wasEvery' : 'todos.schedule.every', { day, time })
  if (s.indefinite) line += ` · ${t('todos.indefinite')}`
  else if (s.expires_at) {
    const d = new Date(s.expires_at)
    if (!Number.isNaN(d.getTime())) {
      line += ` · ${t('todos.row.reviewBy', { date: formatReviewDate(d, locale) })}`
    }
  }
  return line
}

/** Fire-at display in the stored IANA timezone. */
function fireAtDisplay(s: ScheduleRecord, t: Translate, locale: Locale): string {
  if (!s.fire_at) return t('todos.schedule.notScheduled')
  const tz = s.timezone || 'UTC'
  return formatFireAtInTz(s.fire_at, tz, locale)
}

/** Catalog key for the schedule kind label. */
function kindKey(kind: ScheduleRecord['kind']): MessageKey {
  return kind === 'recurring' ? 'todos.kind.recurring' : kind === 'weekly' ? 'todos.kind.weekly' : 'todos.kind.oneShot'
}

/** Derive the actions available for a given status. */
function permittedActions(status: string): 'armed' | 'paused' | 'readonly' {
  if (status === 'armed') return 'armed'
  if (status === 'paused') return 'paused'
  return 'readonly'
}

/** Fired one-shot explanation. */
function terminalExplanation(s: ScheduleRecord): MessageKey | null {
  if (s.kind === 'one_shot' && s.status === 'fired') {
    return 'todos.detail.firedOnce'
  }
  return null
}

export function TodoDetailPage({
  scheduleId,
}: TodoDetailPageProps): JSX.Element {
  const { slug } = useParams<{ slug: string }>()
  const org = slug ?? ''
  const { t, locale } = useTranslation()

  const { data: schedule, isLoading, isError, refetch } = useTodoDetail(org, scheduleId)
  const pauseMutation = usePauseTodo(org)
  const cancelMutation = useCancelTodo(org)
  const editMutation = useEditTodo(org)

  const [pauseOpen, setPauseOpen] = useState(false)
  const [cancelOpen, setCancelOpen] = useState(false)
  const [editOpen, setEditOpen] = useState(false)
  const [validationError, setValidationError] = useState<TodoErrorView | null>(null)
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
          <p className="text-fg-muted mb-4">{t('todos.detail.loadError')}</p>
          <Button onClick={() => refetch()}>{t('todos.retry')}</Button>
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
      const status = (err as { status?: number } | null | undefined)?.status
      if (status === 409) {
        setConflict(true)
      } else {
        setValidationError(classifyTodoError(err, 'todos.error.editFailed'))
      }
    }
  }

  const tz = schedule.timezone || 'UTC'

  return (
    <ContentWrap>
      <Link
        to={`/orgs/${org}/todos`}
        className="text-fg-muted hover:text-fg mb-6 inline-flex items-center gap-1.5 text-sm transition-colors"
      >
        <ArrowLeft size={16} />
        {t('todos.list.title')}
      </Link>

      <div className="mb-6 flex flex-col gap-4 lg:flex-row lg:items-start lg:justify-between">
        <div>
          <h1 className="text-display font-display text-fg leading-tight">
            {schedule.normalized_brief}
          </h1>
          <div className="text-fg-muted mt-3 flex flex-wrap items-center gap-3 text-sm">
            <StatusPill status={schedule.status} />
            <span
              aria-hidden="true"
              className="bg-tier-green-tint text-status-open inline-flex size-5 shrink-0 items-center justify-center rounded text-xs font-semibold"
            >
              {agentInitials(schedule.agent_name)}
            </span>
            <span className="text-fg"><IdentityName canonicalId={schedule.agent_name} /></span>
            <span className="text-fg-subtle font-mono text-xs">
              {schedule.schedule_id}
            </span>
          </div>
        </div>

        {actions !== 'readonly' && (
          <div className="flex items-center gap-2">
            {actions === 'armed' && (
              <Button variant="secondary" onClick={() => setPauseOpen(true)}>
                <Pause size={16} className="mr-1.5" />
                {t('todos.action.pause')}
              </Button>
            )}
            <Button variant="secondary" onClick={() => setEditOpen(true)}>
              <Pencil size={16} className="mr-1.5" />
              {t('todos.action.edit')}
            </Button>
            <Button variant="destructive" onClick={() => setCancelOpen(true)}>
              <X size={16} className="mr-1.5" />
              {t('todos.action.cancel')}
            </Button>
          </div>
        )}
      </div>

      {schedule.fire_at && (
        <div className="border-tier-green bg-tier-green-tint text-status-open mb-6 flex items-center justify-between rounded-lg border px-5 py-3">
          <div>
            <span className="text-overline block font-semibold tracking-wider uppercase">
              {t('todos.row.nextFire')}
            </span>
            <span className="text-lg font-semibold tabular-nums">
              {fireAtDisplay(schedule, t, locale)}
              {tz && <span className="ml-1.5 font-normal opacity-80">{tz}</span>}
            </span>
          </div>
          <div className="text-right">
            <span className="text-overline block font-semibold tracking-wider uppercase">
              {t('todos.detail.recurrence')}
            </span>
            <span className="text-sm font-semibold">{t(kindKey(schedule.kind))}</span>
          </div>
        </div>
      )}

      <div className="flex flex-col gap-6 lg:flex-row">
        <div className="min-w-0 flex-1 space-y-5">
          <div className="border-border bg-bg-raised space-y-4 rounded-lg border p-5">
            <h2 className="text-fg-subtle text-overline font-semibold tracking-wider uppercase">
              {t('todos.detail.schedule')}
            </h2>
            <p className="text-fg text-h1 font-display">
              {describeSchedule(schedule, t, locale)}
            </p>
            <div className="border-border-subtle grid grid-cols-3 gap-4 border-t pt-4">
              <div>
                <span className="text-fg-subtle text-overline block font-semibold tracking-wider uppercase">
                  {t('todos.detail.recurrence')}
                </span>
                <span className="text-fg text-sm font-medium">{t(kindKey(schedule.kind))}</span>
              </div>
              <div>
                <span className="text-fg-subtle text-overline block font-semibold tracking-wider uppercase">
                  {t('todos.detail.timezone')}
                </span>
                <span className="text-fg text-sm font-medium">{tz}</span>
              </div>
              {schedule.kind === 'weekly' && !schedule.indefinite && schedule.expires_at && (
                <div>
                  <span className="text-fg-subtle text-overline block font-semibold tracking-wider uppercase">
                    {t('todos.detail.review')}
                  </span>
                  <span className="text-fg text-sm font-medium">
                    {formatReviewDate(new Date(schedule.expires_at), locale)}
                  </span>
                </div>
              )}
              {Boolean(schedule.indefinite) && (
                <div>
                  <span className="text-fg-subtle text-overline block font-semibold tracking-wider uppercase">
                    {t('todos.detail.review')}
                  </span>
                  <span className="text-fg text-sm font-medium">{t('todos.indefinite')}</span>
                </div>
              )}
            </div>
          </div>

          {schedule.kind === 'weekly' &&
            !schedule.indefinite &&
            schedule.expires_at &&
            schedule.status !== 'expired' && (
              <div className="border-attention-soft bg-attention-soft/30 text-attention-text mb-5 flex items-start gap-2 rounded border px-3 py-2 text-sm">
                <Clock
                  size={14}
                  className="mt-0.5 shrink-0"
                  aria-hidden="true"
                />
                <span>
                  {t('todos.detail.reviewDue', {
                    date: formatReviewDate(new Date(schedule.expires_at), locale),
                  })}
                </span>
              </div>
            )}

          <div className="border-border bg-bg-raised space-y-2 rounded-lg border p-5">
            <h2 className="text-fg-subtle text-overline font-semibold tracking-wider uppercase">
              {t('todos.detail.normalized')}
            </h2>
            <p className="text-fg text-sm">{schedule.normalized_brief}</p>
          </div>

          <div className="border-border bg-bg-raised space-y-2 rounded-lg border p-5">
            <h2 className="text-fg-subtle text-overline font-semibold tracking-wider uppercase">
              {t('todos.detail.original')}
            </h2>
            <blockquote className="border-border-subtle bg-bg-subtle text-fg-muted rounded-lg border-l-4 px-4 py-3 text-sm italic">
              {schedule.source_instruction}
            </blockquote>
          </div>

          {termExplanation && (
            <div className="border-border bg-bg-raised rounded-lg border p-5">
              <p className="text-fg-muted text-sm">{t(termExplanation)}</p>
            </div>
          )}
        </div>

        <div className="space-y-5">
          <div className="border-border bg-bg-raised space-y-3 rounded-lg border p-5">
            <h2 className="text-fg-subtle text-overline font-semibold tracking-wider uppercase">
              {t('todos.detail.activity')}
            </h2>
            {schedule.fire_count > 0 && (
              <div className="flex justify-between gap-2 text-sm">
                <span className="text-fg-subtle">{t('todos.detail.runs')}</span>
                <span className="text-fg font-semibold tabular-nums">
                  {formatCountFor(locale, schedule.fire_count)}
                </span>
              </div>
            )}
            {schedule.last_fired_at && (
              <div className="flex justify-between gap-2 text-sm">
                <span className="text-fg-subtle">{t('todos.detail.lastFired')}</span>
                <span className="text-fg font-semibold tabular-nums">
                  {fmtDate(schedule.last_fired_at, locale)}
                </span>
              </div>
            )}
            {schedule.spawned_task_ids && schedule.spawned_task_ids.length > 0 && (
              <div className="flex flex-wrap gap-1.5">
                {schedule.spawned_task_ids.map((taskId) => (
                  <Link
                    key={taskId}
                    to={`/orgs/${org}/tasks/${taskId}`}
                    className="bg-bg-subtle text-fg-muted hover:text-fg inline-flex items-center rounded px-2 py-1 font-mono text-xs"
                  >
                    {taskId}
                  </Link>
                ))}
              </div>
            )}
            <Link
              to={`/orgs/${org}/audit?task_id=${schedule.schedule_id}`}
              className="text-accent inline-flex items-center gap-1 text-sm hover:underline"
            >
              {t('todos.detail.viewActivity')}
              <ExternalLink size={12} aria-hidden="true" />
            </Link>
            <span className="text-fg-subtle block font-mono text-xs">
              task_id={schedule.schedule_id}
            </span>
          </div>

          <div className="border-border bg-bg-raised space-y-2 rounded-lg border p-5">
            <h2 className="text-fg-subtle text-overline mb-2 font-semibold tracking-wider uppercase">
              {t('todos.detail.recordDetails')}
            </h2>
            <dl className="space-y-2 text-xs">
              <div className="flex justify-between gap-2">
                <dt className="text-fg-subtle">{t('todos.detail.created')}</dt>
                <dd className="text-fg-muted">{fmtDate(schedule.created_at, locale)}</dd>
              </div>
              <div className="flex justify-between gap-2">
                <dt className="text-fg-subtle">{t('todos.detail.updated')}</dt>
                <dd className="text-fg-muted">{fmtDate(schedule.updated_at, locale)}</dd>
              </div>
              <div className="flex justify-between gap-2">
                <dt className="text-fg-subtle">{t('todos.detail.team')}</dt>
                <dd className="text-fg-muted">{schedule.team}</dd>
              </div>
              <div className="flex justify-between gap-2">
                <dt className="text-fg-subtle">{t('todos.detail.scheduleId')}</dt>
                <dd className="text-fg-muted font-mono">{schedule.schedule_id}</dd>
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
            title={t('todos.pause.title')}
            description={t('todos.pause.description')}
            confirmLabel={t('todos.action.pause')}
            confirmVariant="default"
            loading={pauseMutation.isPending}
            onConfirm={handlePause}
          />

          <ConfirmDialog
            open={cancelOpen}
            onOpenChange={setCancelOpen}
            title={t('todos.cancel.title')}
            description={t('todos.cancel.description')}
            confirmLabel={t('todos.cancel.confirm')}
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
            loading={editMutation.isPending}
          />
        </>
      )}
    </ContentWrap>
  )
}
