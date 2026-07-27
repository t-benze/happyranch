/**
 * Todos feature string constants.
 */
import type { ScheduleStatus } from '@/lib/api/types'

export const TODO_STRINGS = {
  pageTitle: 'Todos',
  eyebrow: 'Agent commitments',
  subtitle: 'Scheduled commitments agents created from your instructions.',
  trustLine:
    'Agents can create Todos from your instructions. You can review, pause, edit, or cancel any Todo here.',
  filterAll: 'All',
  filterActive: 'Active',
  filterPaused: 'Paused',
  filterNeedsAttention: 'Needs attention',
  filterHistory: 'History',
  filterAllAgents: 'All agents',
  sectionActive: 'Active',
  sectionNeedsAttention: 'Needs attention',
  sectionPaused: 'Paused',
  sectionHistory: 'History',
  emptyTitle: 'No Todos yet',
  emptyBody:
    'When agents create scheduled commitments from your instructions, they will appear here.',
  filteredEmptyTitle: 'No matching Todos',
  filteredEmptyBody:
    'Try a different filter or agent to see scheduled commitments.',
  nextFireLabel: 'Next fire',
  firingNow: 'Firing now',
  runsLabel: 'runs',
  indefiniteLabel: 'Indefinite',
  reviewByLabel: 'Review by',
  pausedLabel: 'Paused',
  armedLabel: 'Armed',
  firingLabel: 'Firing now',
  firedLabel: 'Completed',
  failedLabel: 'Needs attention',
  timeoutLabel: 'Timed out',
  expiredLabel: 'Review expired',
  cancelledLabel: 'Cancelled',
} as const

/** Human-readable status label for a schedule status value. */
export function statusLabel(status: ScheduleStatus): string {
  const map: Record<ScheduleStatus, string> = {
    armed: TODO_STRINGS.armedLabel,
    firing: TODO_STRINGS.firingLabel,
    fired: TODO_STRINGS.firedLabel,
    paused: TODO_STRINGS.pausedLabel,
    cancelled: TODO_STRINGS.cancelledLabel,
    expired: TODO_STRINGS.expiredLabel,
    failed: TODO_STRINGS.failedLabel,
    timeout: TODO_STRINGS.timeoutLabel,
  }
  return map[status] ?? status
}

/** Tone classes for schedule status pills, reusing the semantic tone vocabulary. */
export function statusPillClass(status: ScheduleStatus): string {
  // Reuse existing semantic token classes — no arbitrary values.
  switch (status) {
    case 'armed':
    case 'firing':
      return 'text-status-open bg-tier-green-tint' // positive / green
    case 'failed':
    case 'timeout':
      return 'text-attention-text bg-attention-soft' // attention / amber
    case 'fired':
      return 'text-accent-text bg-accent-soft' // completed — accent green (not bold green)
    case 'paused':
    case 'cancelled':
    case 'expired':
    default:
      return 'text-status-archived border border-border-default bg-transparent' // neutral
  }
}

/** Groups for the index filter tabs in display order. */
export type FilterGroup = 'all' | 'active' | 'paused' | 'needs_attention' | 'history'

export const FILTER_GROUPS: { key: FilterGroup; label: string }[] = [
  { key: 'all', label: TODO_STRINGS.filterAll },
  { key: 'active', label: TODO_STRINGS.filterActive },
  { key: 'paused', label: TODO_STRINGS.filterPaused },
  { key: 'needs_attention', label: TODO_STRINGS.filterNeedsAttention },
  { key: 'history', label: TODO_STRINGS.filterHistory },
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
  label: string
  statuses: ScheduleStatus[]
}[] = [
  { key: 'active', label: TODO_STRINGS.sectionActive, statuses: ['armed', 'firing'] },
  {
    key: 'needs_attention',
    label: TODO_STRINGS.sectionNeedsAttention,
    statuses: ['failed', 'timeout'],
  },
  { key: 'paused', label: TODO_STRINGS.sectionPaused, statuses: ['paused'] },
  {
    key: 'history',
    label: TODO_STRINGS.sectionHistory,
    statuses: ['fired', 'expired', 'cancelled'],
  },
]
