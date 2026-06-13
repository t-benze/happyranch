import { useEffect, useMemo, useRef, useState } from 'react';
import { useParams } from 'react-router-dom';
import { FilterSidebar, type FilterGroup } from '@/design-system/patterns/FilterSidebar';
import { TaskCard } from '@/design-system/patterns/TaskCard';
import { EmptyState } from '@/design-system/patterns/EmptyState';
import { useTasksInfiniteList, useTasksRoutes } from '@/hooks/tasks';
import { useDensity } from '@/hooks/density';
import { TaskDetailPane } from './TaskDetailPane';

const STATUSES: FilterGroup['options'] = [
  { value: 'pending', label: 'Pending' },
  { value: 'in_progress', label: 'In progress' },
  { value: 'blocked', label: 'Blocked' },
  { value: 'completed', label: 'Completed' },
  { value: 'failed', label: 'Failed' },
  { value: 'resolved_superseded', label: 'Resolved (superseded)' },
];

export function TasksPage(): JSX.Element {
  const { task_id: openTaskId } = useParams<{ task_id: string }>();
  const [filters, setFilters] = useState<Record<string, string | null>>({
    status: null,
    team: null,
  });
  const { density } = useDensity();
  const routes = useTasksRoutes();
  const tasksQuery = useTasksInfiniteList(
    filters.status ? { status: filters.status } : undefined,
  );

  const allTasks = useMemo(
    () => tasksQuery.data?.pages.flatMap((p) => p.tasks) ?? [],
    [tasksQuery.data],
  );

  const filtered = useMemo(
    () => (filters.team ? allTasks.filter((t) => t.team === filters.team) : allTasks),
    [allTasks, filters.team],
  );

  const teams = useMemo(() => {
    const set = new Set<string>();
    allTasks.forEach((t) => set.add(t.team));
    return [...set].sort();
  }, [allTasks]);

  const groups: FilterGroup[] = [
    { key: 'status', label: 'Status', options: STATUSES },
    { key: 'team', label: 'Team', options: teams.map((t) => ({ value: t, label: t })) },
  ];

  // Sentinel observer: when the bottom marker scrolls into view, request the
  // next page. Guards against re-entrancy and races so a fast scroll doesn't
  // queue multiple fetches against the same cursor.
  const sentinelRef = useRef<HTMLDivElement | null>(null);
  const { fetchNextPage, hasNextPage, isFetchingNextPage } = tasksQuery;
  useEffect(() => {
    const node = sentinelRef.current;
    if (!node || !hasNextPage) return;
    const obs = new IntersectionObserver(
      (entries) => {
        if (entries[0]?.isIntersecting && !isFetchingNextPage) {
          fetchNextPage();
        }
      },
      // rootMargin pre-loads the next page slightly before the sentinel
      // hits the viewport — feels instant on fast scrolls.
      { rootMargin: '200px' },
    );
    obs.observe(node);
    return () => obs.disconnect();
  }, [fetchNextPage, hasNextPage, isFetchingNextPage]);

  return (
    <div className="flex h-full">
      <FilterSidebar groups={groups} value={filters} onChange={setFilters} />
      <main className="bg-surface-canvas flex-1 overflow-y-auto p-4">
        {tasksQuery.isLoading ? (
          <p className="text-fg-muted">Loading…</p>
        ) : filtered.length === 0 ? (
          <EmptyState title="No tasks" body="No tasks match the current filters." />
        ) : (
          <>
            <ul className="space-y-2">
              {filtered.map((t) => (
                <li key={t.task_id}>
                  <TaskCard
                    task={t}
                    to={routes.detail(t.task_id)}
                    active={openTaskId === t.task_id}
                    density={density}
                  />
                </li>
              ))}
            </ul>
            <div ref={sentinelRef} aria-hidden className="h-1" />
            {isFetchingNextPage && (
              <p className="text-fg-muted py-3 text-center text-sm">Loading more…</p>
            )}
            {!hasNextPage && allTasks.length > 0 && (
              <p className="text-fg-muted py-3 text-center text-xs">End of list</p>
            )}
          </>
        )}
      </main>
      {openTaskId && <TaskDetailPane taskId={openTaskId} />}
    </div>
  );
}
