/**
 * TodosPage — the main list view for agent Todos (scheduled commitments).
 *
 * Routes at /orgs/:slug/todos. When the route includes :scheduleId, the
 * detail pane is rendered instead.
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
  const { slug, scheduleId: routeScheduleId } = useParams<{
    slug: string
    scheduleId?: string
  }>()
  const org = slug ?? ''
  const [activeFilter, setActiveFilter] = useState<FilterGroup>('all')
  const [agentFilter, setAgentFilter] = useState<string | undefined>(undefined)

  const { data, isLoading, isError, refetch } = useTodoList(org, {
    agent: agentFilter,
    limit: 200,
  })

  const allSchedules: ScheduleRecord[] = data?.schedules ?? []

  const agentNames = Array.from(new Set(allSchedules.map((s) => s.agent_name))).sort()

  const filteredSchedules =
    activeFilter === 'all'
      ? allSchedules
      : allSchedules.filter((s) => {
          const groupStatuses = SECTION_ORDER.find(
            (sec) => sec.key === activeFilter,
          )?.statuses
          return groupStatuses?.includes(s.status)
        })

  const sections = SECTION_ORDER.map((sec) => {
    const items = filteredSchedules.filter((s) => sec.statuses.includes(s.status))
    return { ...sec, items }
  }).filter((sec) => sec.items.length > 0)

  const activeCount = allSchedules.filter((s) =>
    ['armed', 'firing'].includes(s.status),
  ).length
  const attentionCount = allSchedules.filter((s) =>
    ['failed', 'timeout'].includes(s.status),
  ).length

  if (routeScheduleId) {
    return <TodoDetailPage scheduleId={routeScheduleId} />
  }

  return (
    <ContentWrap>
      <div className="mb-5">
        <p className="text-fg-subtle mb-1 text-xs font-medium tracking-wider uppercase">
          {TODO_STRINGS.eyebrow}
        </p>
        <h1 className="text-h1 text-fg">{TODO_STRINGS.pageTitle}</h1>
        <p className="text-fg-muted mt-2 text-base">{TODO_STRINGS.subtitle}</p>
        <p className="text-fg-subtle mt-1.5 text-sm">{TODO_STRINGS.trustLine}</p>
      </div>

      <div className="mb-5 flex flex-wrap items-center justify-between gap-3">
        <div className="bg-bg-subtle inline-flex rounded-lg p-0.5">
          {FILTER_GROUPS.map((fg) => (
            <button
              key={fg.key}
              type="button"
              onClick={() => setActiveFilter(fg.key)}
              className={cn(
                'rounded-md px-3 py-1.5 text-sm font-medium transition-colors',
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
          {agentNames.length > 0 && (
            <select
              value={agentFilter ?? ''}
              onChange={(e) => setAgentFilter(e.target.value || undefined)}
              className="border-border bg-bg-raised text-fg-muted focus:ring-accent rounded-md border px-2.5 py-1.5 text-sm focus:ring-2 focus:outline-none"
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

          <span className="text-fg-subtle text-xs">
            {activeCount > 0 && `${activeCount} active`}
            {activeCount > 0 && attentionCount > 0 && ' · '}
            {attentionCount > 0 && `${attentionCount} needs attention`}
          </span>
        </div>
      </div>

      {isLoading && (
        <div className="space-y-3">
          {[1, 2, 3].map((i) => (
            <div
              key={i}
              className="border-border bg-bg-raised animate-pulse rounded-lg border px-5 py-4"
            >
              <div className="flex items-start justify-between gap-4">
                <div className="flex items-center gap-2.5">
                  <div className="bg-bg-subtle h-5 w-16 rounded-full" />
                  <div className="bg-bg-subtle h-5 w-72 rounded" />
                </div>
                <div className="bg-bg-subtle h-7 w-24 rounded" />
              </div>
              <div className="mt-2 flex items-center gap-2">
                <div className="bg-bg-subtle size-4 rounded-full" />
                <div className="bg-bg-subtle h-4 w-44 rounded" />
              </div>
            </div>
          ))}
        </div>
      )}

      {isError && !isLoading && (
        <div className="flex flex-col items-center justify-center py-16 text-center">
          <AlertCircle size={32} className="text-feedback-danger mb-3" aria-hidden="true" />
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

      {!isLoading && !isError && allSchedules.length === 0 && (
        <EmptyState
          icon={<Clock size={32} />}
          title={TODO_STRINGS.emptyTitle}
          body={<p>{TODO_STRINGS.emptyBody}</p>}
        />
      )}

      {!isLoading &&
        !isError &&
        allSchedules.length > 0 &&
        filteredSchedules.length === 0 && (
          <EmptyState
            title={TODO_STRINGS.filteredEmptyTitle}
            body={<p>{TODO_STRINGS.filteredEmptyBody}</p>}
          />
        )}

      {!isLoading && !isError && filteredSchedules.length > 0 && (
        <div className="space-y-4">
          {sections.map((sec) => (
            <div key={sec.key}>
              <div className="text-fg-subtle px-5 py-1 text-xs font-normal tracking-wider uppercase">
                {sec.label} · {sec.items.length}
              </div>
              <div className="border-border bg-bg-raised overflow-hidden rounded-lg border">
                {sec.items.map((schedule) => (
                  <TodoRow key={schedule.schedule_id} schedule={schedule} />
                ))}
              </div>
            </div>
          ))}
        </div>
      )}
    </ContentWrap>
  )
}
