/**
 * Tasks list — Direction-A Pasture, roots-only dense list.
 *
 * Group by Status / Agent / Thread. Each group renders as a Pasture card
 * with serif section headings. Resolved groups are visually dimmed.
 * Status pills follow ds.css .tag (rounded-pill, led dot).
 *
 * Per founder ruling: NO in-list 'show subtasks' toggle. The list is
 * roots-only; execution subtasks live on the Task detail surface.
 */
import { useEffect, useMemo, useRef, useState } from 'react';
import { useParams } from 'react-router-dom';
import { Tabs, TabsList, TabsTrigger } from '@/design-system/primitives/Tabs';
import { TaskCard } from '@/design-system/patterns/TaskCard';
import { EmptyState } from '@/design-system/patterns/EmptyState';
import { useTasksInfiniteList, useTasksRoutes } from '@/hooks/tasks';
import { useDensity } from '@/hooks/density';
import { TaskDetailPane } from './TaskDetailPane';
import type { TaskRecord } from '@/lib/api/types';

type GroupBy = 'status' | 'agent' | 'thread';

const GROUP_BY_OPTIONS: { value: GroupBy; label: string }[] = [
  { value: 'status', label: 'Status' },
  { value: 'agent', label: 'Agent' },
  { value: 'thread', label: 'Thread' },
];

/** Pasture group section heading — serif display, muted for resolved groups. */
function GroupHeading({
  label,
  count,
  dimmed,
}: {
  label: string;
  count: number;
  dimmed?: boolean;
}): JSX.Element {
  return (
    <h2
      className={
        dimmed
          ? 'font-display text-text-muted text-lg font-medium'
          : 'font-display text-text-primary text-lg font-medium'
      }
    >
      {label}
      <span className="text-text-muted ml-2 font-mono text-sm font-normal tabular-nums">
        {count}
      </span>
    </h2>
  );
}

function groupKey(task: TaskRecord, by: GroupBy): string {
  switch (by) {
    case 'status':
      return task.status;
    case 'agent':
      return task.assigned_agent || 'Unassigned';
    case 'thread': {
      // Extract thread identifier from task_id prefix — tasks dispatched
      // from a thread have the thread id in metadata or brief; otherwise
      // fall back to team.
      return task.team;
    }
  }
}

function groupLabel(key: string, by: GroupBy): string {
  if (by === 'status') {
    const map: Record<string, string> = {
      pending: 'Pending',
      in_progress: 'In progress',
      blocked: 'Blocked',
      completed: 'Completed',
      failed: 'Failed',
      resolved_superseded: 'Resolved',
    };
    return map[key] ?? key;
  }
  return key;
}

function isResolvedGroup(key: string, by: GroupBy): boolean {
  if (by === 'status') {
    return key === 'completed' || key === 'failed' || key === 'resolved_superseded';
  }
  return false;
}

const GROUP_ORDER_STATUS: Record<string, number> = {
  in_progress: 0,
  pending: 1,
  blocked: 2,
  completed: 3,
  failed: 4,
  resolved_superseded: 5,
};

export function TasksPage(): JSX.Element {
  const { task_id: openTaskId } = useParams<{ task_id: string }>();
  const [groupBy, setGroupBy] = useState<GroupBy>('status');
  const { density } = useDensity();
  const routes = useTasksRoutes();
  const tasksQuery = useTasksInfiniteList();

  const allTasks = useMemo(
    () => tasksQuery.data?.pages.flatMap((p) => p.tasks) ?? [],
    [tasksQuery.data],
  );

  // Group tasks by the active dimension, sorted by group priority then recency.
  const groups = useMemo(() => {
    const map = new Map<string, TaskRecord[]>();
    for (const t of allTasks) {
      const k = groupKey(t, groupBy);
      const list = map.get(k);
      if (list) list.push(t);
      else map.set(k, [t]);
    }
    // Sort within each group by updated_at desc (most recent first).
    for (const [, tasks] of map) {
      tasks.sort(
        (a, b) =>
          new Date(b.updated_at).getTime() - new Date(a.updated_at).getTime(),
      );
    }
    // Sort groups: by explicit order for status, alpha for others.
    const entries = [...map.entries()];
    if (groupBy === 'status') {
      entries.sort(
        (a, b) =>
          (GROUP_ORDER_STATUS[a[0]] ?? 99) -
          (GROUP_ORDER_STATUS[b[0]] ?? 99),
      );
    } else {
      entries.sort((a, b) => a[0].localeCompare(b[0]));
    }
    return entries;
  }, [allTasks, groupBy]);

  // Sentinel observer for infinite scroll.
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
      { rootMargin: '200px' },
    );
    obs.observe(node);
    return () => obs.disconnect();
  }, [fetchNextPage, hasNextPage, isFetchingNextPage]);

  return (
    <div className="bg-surface-canvas flex h-full flex-col">
      {/* Page title + group-by selector */}
      <header className="border-border-default shrink-0 border-b px-6 py-5">
        <h1 className="font-display text-display text-text-primary font-medium">
          Tasks
        </h1>
        <Tabs
          className="mt-3"
          value={groupBy}
          onValueChange={(v) => setGroupBy(v as GroupBy)}
          aria-label="Group by"
        >
          <TabsList>
            {GROUP_BY_OPTIONS.map((opt) => {
              const count = new Set(allTasks.map((t) => groupKey(t, opt.value)))
                .size;
              return (
                <TabsTrigger key={opt.value} value={opt.value}>
                  {opt.label}
                  <span className="text-text-muted ml-1 text-xs tabular-nums">
                    {tasksQuery.isLoading ? '…' : count}
                  </span>
                </TabsTrigger>
              );
            })}
          </TabsList>
        </Tabs>
      </header>

      {/* Task list */}
      <main className="min-h-0 flex-1 overflow-y-auto px-6 py-4">
        {tasksQuery.isLoading ? (
          <p className="text-text-muted py-6 text-center text-sm">Loading…</p>
        ) : allTasks.length === 0 ? (
          <EmptyState title="No tasks" body="No tasks match the current filters." />
        ) : (
          <div className="mx-auto max-w-3xl space-y-6">
            {groups.map(([key, tasks]) => {
              const dimmed = isResolvedGroup(key, groupBy);
              return (
                <section key={key} className={dimmed ? 'opacity-60' : undefined}>
                  <GroupHeading
                    label={groupLabel(key, groupBy)}
                    count={tasks.length}
                    dimmed={dimmed}
                  />
                  <ul className="mt-2 space-y-1.5">
                    {tasks.map((t) => (
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
                </section>
              );
            })}
            <div ref={sentinelRef} aria-hidden className="h-1" />
            {isFetchingNextPage && (
              <p className="text-text-muted py-3 text-center text-sm">
                Loading more…
              </p>
            )}
            {!hasNextPage && allTasks.length > 0 && (
              <p className="text-text-muted py-4 text-center text-xs">
                End of list
              </p>
            )}
          </div>
        )}
      </main>

      {openTaskId && <TaskDetailPane taskId={openTaskId} />}
    </div>
  );
}
