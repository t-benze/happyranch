/**
 * Todos copy helpers and status vocabulary (THR-118 W4b).
 *
 * All Todos product copy lives in the typed catalog under `todos.*`. This file
 * maps schedule status tokens, filter groups and daemon error codes onto
 * catalog keys. Unknown status tokens render verbatim (daemon values are never
 * translated).
 *
 * Edit-save errors use the same diagnostic boundary as the Jobs
 * `classifyJobError` (W3b-2): the detail page holds a locale-neutral
 * `TodoErrorView` in state and renders it through `t` on every render, so a
 * mapped message re-translates in place on a locale switch while daemon
 * diagnostics stay byte-for-byte verbatim.
 */
import type { ScheduleStatus } from '@/lib/api/types'
import type { MessageKey, MessageParams } from '@/lib/i18n'

export type Translate = (key: MessageKey, params?: MessageParams) => string

const STATUS_KEYS: Record<string, MessageKey> = {
  armed: 'todos.status.armed',
  firing: 'todos.status.firing',
  fired: 'todos.status.fired',
  paused: 'todos.status.paused',
  cancelled: 'todos.status.cancelled',
  expired: 'todos.status.expired',
  failed: 'todos.status.failed',
  timeout: 'todos.status.timeout',
}

/** Localized label for a known schedule status; an unknown token stays verbatim. */
export function statusLabel(status: ScheduleStatus, t: Translate): string {
  return Object.prototype.hasOwnProperty.call(STATUS_KEYS, status)
    ? t(STATUS_KEYS[status])
    : status
}

/** Groups for the index filter tabs in display order. */
export type FilterGroup = 'all' | 'active' | 'paused' | 'needs_attention' | 'history'

export const FILTER_GROUPS: { key: FilterGroup; labelKey: MessageKey }[] = [
  { key: 'all', labelKey: 'todos.filter.all' },
  { key: 'active', labelKey: 'todos.group.active' },
  { key: 'paused', labelKey: 'todos.group.paused' },
  { key: 'needs_attention', labelKey: 'todos.group.needsAttention' },
  { key: 'history', labelKey: 'todos.group.history' },
]

/** Which status values belong to each filter group. */
export const GROUP_STATUSES: Record<FilterGroup, ScheduleStatus[]> = {
  all: ['armed', 'firing', 'fired', 'paused', 'cancelled', 'expired', 'failed', 'timeout'],
  active: ['armed', 'firing'],
  paused: ['paused'],
  needs_attention: ['failed', 'timeout'],
  history: ['fired', 'expired', 'cancelled'],
}

/** Section labels for the grouped-list display order. */
export const SECTION_ORDER: {
  key: FilterGroup
  labelKey: MessageKey
  statuses: ScheduleStatus[]
}[] = [
  { key: 'active', labelKey: 'todos.group.active', statuses: ['armed', 'firing'] },
  {
    key: 'needs_attention',
    labelKey: 'todos.group.needsAttention',
    statuses: ['failed', 'timeout'],
  },
  { key: 'paused', labelKey: 'todos.group.paused', statuses: ['paused'] },
  {
    key: 'history',
    labelKey: 'todos.group.history',
    statuses: ['fired', 'expired', 'cancelled'],
  },
]

const SHORT_DAY_KEYS: Record<string, MessageKey> = {
  Mon: 'todos.weekdayShort.mon',
  Tue: 'todos.weekdayShort.tue',
  Wed: 'todos.weekdayShort.wed',
  Thu: 'todos.weekdayShort.thu',
  Fri: 'todos.weekdayShort.fri',
  Sat: 'todos.weekdayShort.sat',
  Sun: 'todos.weekdayShort.sun',
}

/** Localized short weekday for a weekly `recurrence.day` token; unknown stays verbatim. */
export function shortDayLabel(day: string, t: Translate): string {
  return Object.prototype.hasOwnProperty.call(SHORT_DAY_KEYS, day) ? t(SHORT_DAY_KEYS[day]) : day
}

const TODOS_ERROR_KEYS: Record<string, MessageKey> = {
  invalid_fire_at: 'todos.error.invalidFireAt',
  explicit_null: 'todos.error.explicitNull',
  not_found: 'todos.error.notFound',
}

export type TodoErrorView =
  | { kind: 'message'; key: MessageKey; params?: MessageParams }
  | { kind: 'raw'; text: string }

/**
 * Classify a thrown value as a descriptor instead of an English string:
 *  - a recognized product code -> its catalog key;
 *  - any other non-empty code -> the raw code, verbatim;
 *  - no/empty code but a non-empty string diagnostic -> that text, verbatim
 *    (an ApiError-shaped value's string `detail`, or a plain Error's message /
 *    a thrown string; ApiError's synthetic 'API <status>' message is never one);
 *  - otherwise -> the localized `fallback`, so an error is never blank.
 */
export function classifyTodoError(err: unknown, fallback: MessageKey): TodoErrorView {
  const code = (err as { code?: string } | null | undefined)?.code
  if (code) {
    if (Object.prototype.hasOwnProperty.call(TODOS_ERROR_KEYS, code)) {
      return { kind: 'message', key: TODOS_ERROR_KEYS[code] }
    }
    return { kind: 'raw', text: code }
  }
  const diagnostic = rawDiagnostic(err)
  return diagnostic === null ? { kind: 'message', key: fallback } : { kind: 'raw', text: diagnostic }
}

function rawDiagnostic(err: unknown): string | null {
  let text: unknown = null
  if (typeof err === 'string') text = err
  else if (err !== null && typeof err === 'object' && 'detail' in err) text = err.detail
  else if (err instanceof Error) text = err.message
  return typeof text === 'string' && text.trim() !== '' ? text : null
}

export function renderTodoError(view: TodoErrorView, t: Translate): string {
  return view.kind === 'raw' ? view.text : t(view.key, view.params)
}
