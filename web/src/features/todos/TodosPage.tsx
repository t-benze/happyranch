/**
 * TodosPage — the main list view for agent Todos (scheduled commitments).
 * Routes at /orgs/:slug/todos.
 *
 * When the route includes a :scheduleId param (/orgs/:slug/todos/:scheduleId),
 * the detail pane is shown.  Row clicks navigate via React Router Link
 * (no nested interactive controls).  Back navigates to the bare list route.
 *
 * Time displays use the stored IANA timezone via helpers in ./timezone.ts.
 *
 * Compose from existing design-system tokens and patterns; no arbitrary
 * values. Fidelity target: the reference design at 1440×900.
 *
 * States: loading, error (retryable), empty, filtered-empty, populated
 * (grouped by status section).
 */
import { useState } from 'react'
import { useParams } from 'react-router-dom'
import { AlertCircle, Clock, RefreshCw } from 'lucide-react'
import { ContentWrap } from '@/design-system/layouts/ContentWrap/ContentWrap'
import { Button } from '@/design-system/primitives/Button'
import { EmptyState } from '@/design-system/patterns/EmptyState'
import { cn } from '@/lib/utils'
import { useTodoList } from './hooks'
import { TodoRow } from './components/TodoRow'
import {
  TODO_STRINGS,
  FILTER_GROUPS,
  SECTION_ORDER,
  type FilterGroup,
} from './strings'
import type { ScheduleRecord } from '@/lib/api/types'
import { TodoDetailPage } from './TodoDetailPage'

export function TodosPage(): JSX.Element {
  const { slug, scheduleId: routeScheduleId } = useParams<{ slug: string; scheduleId?: string }>()
  const org = slug ?? ''
  const [activeFilter, setActiveFilter] = useState<FilterGroup>('all')
  const [agentFilter, setAgentFilter] = useState<string | undefined>(undefined)

  const { data, isLoading, isError, refetch } = useTodoList(org, {
    agent: agentFilter,
    limit: 200,
  })

  const allSchedules: ScheduleRecord[] = data?.schedules ?? []

  // Collect unique agent names for the agent filter dropdown
  const agentNames = Array.from(new Set(allSchedules.map((s) => s.agent_name))).sort()

  // Filter by group
  const filteredSchedules =
    activeFilter === 'all'
      ? allSchedules
      : allSchedules.filter((s) => {
          const groupStatuses = SECTION_ORDER.find(
            (sec) => sec.key === activeFilter,
          )?.statuses
          return groupStatuses?.includes(s.status)
        })

  // Group into sections for display
  const sections = SECTION_ORDER.map((sec) => {
    const items = filteredSchedules.filter((s) => sec.statuses.includes(s.status))
    return { ...sec, items }
  }).filter((sec) => sec.items.length > 0)

  // Counts for the summary line
  const activeCount = allSchedules.filter((s) =>
    ['armed', 'firing'].includes(s.status),
  ).length
  const attentionCount = allSchedules.filter((s) =>
    ['failed', 'timeout'].includes(s.status),
  ).length

  // If detail is selected via route, show the detail page.
  // Back navigates to /orgs/:slug/todos (the list route).
  if (routeScheduleId) {
    return (
      <TodoDetailPage
        scheduleId={routeScheduleId}
      />
    )
  }

  return (
    <ContentWrap>
      {/* Page header */}
      <div className="mb-5">
        <p className="text-xs font-medium text-fg-subtle uppercase tracking-wider mb-1">
          {TODO_STRINGS.eyebrow}
        </p>
        <h1 className="text-h1 text-fg font-display">
          {TODO_STRINGS.pageTitle}
        </h1>
        <p className="text-base text-fg-muted mt-2">
          {TODO_STRINGS.subtitle}
        </p>
        <p className="text-sm text-fg-subtle mt-1.5">
          {TODO_STRINGS.trustLine}
        </p>
      </div>

      {/* Filter bar */}
      <div className="flex flex-wrap items-center justify-between gap-3 mb-5">
        <div className="inline-flex rounded-xl bg-bg-subtle p-0.5">
          {FILTER_GROUPS.map((fg) => (
            <button
              key={fg.key}
              type="button"
              onClick={() => setActiveFilter(fg.key)}
              className={cn(
                'rounded-lg px-3 py-1.5 text-sm font-medium transition-colors',
                activeFilter === fg.key
                  ? 'bg-bg-raised text-fg shadow-sm'
                  : 'text-fg-muted hover:text-fg',
              )}
            >
              {fg.label}
            </button>
          ))}
        </div>

        <div className="flex items-center gap-3">
          {/* Agent filter */}
          {agentNames.length > 0 && (
            <select
              value={agentFilter ?? ''}
              onChange={(e) => setAgentFilter(e.target.value || undefined)}
              className="rounded-md border border-border bg-bg-raised px-2.5 py-1.5 text-sm text-fg-muted focus:outline-none focus:ring-2 focus:ring-accent"
              aria-label="Filter by agent"
            >
              <option value="">{TODO_STRINGS.filterAllAgents}</option>
              {agentNames.map((name) => (
                <option key={name} value={name}>
                  {name}
                </option>
              ))}
            </select>
          )}

          {/* Summary line */}
          <span className="text-xs text-fg-subtle">
            {activeCount > 0 && `${activeCount} active`}
            {activeCount > 0 && attentionCount > 0 && ' · '}
            {attentionCount > 0 && `${attentionCount} needs attention`}
          </span>
        </div>
      </div>

      {/* Loading state */}
      {isLoading && (
        <div className="space-y-3">
          {[1, 2, 3].map((i) => (
            <div
              key={i}
              className="rounded-2xl border border-border bg-bg-raised px-5 py-4 animate-pulse"
            >
              <div className="flex items-start justify-between gap-4">
                <div className="flex items-center gap-2.5">
                  <div className="h-5 w-16 rounded-full bg-bg-subtle" />
                  <div className="h-5 w-72 rounded bg-bg-subtle" />
                </div>
                <div className="h-7 w-24 rounded bg-bg-subtle" />
              </div>
              <div className="mt-2 flex items-center gap-2">
                <div className="size-4 rounded-full bg-bg-subtle" />
                <div className="h-4 w-44 rounded bg-bg-subtle" />
              </div>
            </div>
          ))}
        </div>
      )}

      {/* Error state */}
      {isError && !isLoading && (
        <div className="flex flex-col items-center justify-center py-16 text-center">
          <AlertCircle size={32} className="text-feedback-danger mb-3" aria-hidden />
          <h3 className="text-h3 text-fg">Failed to load Todos</h3>
          <p className="text-body text-fg-muted mt-2 mb-5">
            The server returned an error. You can try again.
          </p>
          <Button onClick={() => refetch()}>
            <RefreshCw size={14} className="mr-1.5" />
            Retry
          </Button>
        </div>
      )}

      {/* Empty state */}
      {!isLoading && !isError && allSchedules.length === 0 && (
        <EmptyState
          icon={<Clock size={32} />}
          title={TODO_STRINGS.emptyTitle}
          body={<p>{TODO_STRINGS.emptyBody}</p>}
        />
      )}

      {/* Filtered-empty state */}
      {!isLoading && !isError && allSchedules.length > 0 && filteredSchedules.length === 0 && (
        <EmptyState
          title={TODO_STRINGS.filteredEmptyTitle}
          body={<p>{TODO_STRINGS.filteredEmptyBody}</p>}
        />
      )}

      {/* Populated: grouped sections */}
      {!isLoading && !isError && filteredSchedules.length > 0 && (
        <div className="space-y-4">
          {sections.map((sec) => (
            <div key={sec.key}>
              <div className="px-5 py-1 text-xs font-normal text-fg-subtle uppercase tracking-wider">
                {sec.label} · {sec.items.length}
              </div>
              <div className="rounded-2xl border border-border bg-bg-raised overflow-hidden">
                {sec.items.map((schedule) => (
                  <TodoRow
                    key={schedule.schedule_id}
                    schedule={schedule}
                  />
                ))}
              </div>
            </div>
          ))}
        </div>
      )}
    </ContentWrap>
  )
}
