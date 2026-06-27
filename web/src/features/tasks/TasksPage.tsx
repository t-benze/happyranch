/**
 * Tasks list — Direction-A Pasture, roots-only dense list.
 *
 * Group by Status / Agent / Thread. Each group renders as a Pasture card
 * with serif section headings. Resolved groups are visually dimmed.
 * Status pills follow ds.css .tag (rounded-pill, led dot).
 *
 * Per founder ruling: NO in-list 'show subtasks' toggle. The list is
 * roots-only; execution subtasks live on the Task detail surface.
 *
 * Driven by GET /tasks/roots (roots-only invariant). Cursor pagination
 * via next_cursor with IntersectionObserver sentinel.
 */
import { useEffect, useMemo, useRef, useState } from 'react';
import { Tabs, TabsList, TabsTrigger } from '@/design-system/primitives/Tabs';
import { EmptyState } from '@/design-system/patterns/EmptyState';
import {
  TaskListColumnHeader,
  TaskListRow,
  severityRollupStatus,
} from './TaskListRow';
import { useTasksRootsInfinite, useTasksRoutes } from '@/hooks/tasks';
import type { TaskRecord } from '@/lib/api/types';

type GroupBy = 'status' | 'agent' | 'thread';

const GROUP_BY_OPTIONS: { value: GroupBy; label: string }[] = [
  { value: 'status', label: 'Status' },
  { value: 'agent', label: 'Agent' },
  { value: 'thread', label: 'Thread' },
];

/**
 * Colored status dot per group. Status groups map to the same semantic tokens
 * StatusBadge uses; non-status groups (agent / thread) carry a neutral dot —
 * we never claim a status color for a dimension that has no single status.
 */
type GroupDot =
  | 'in_progress'
  | 'pending'
  | 'escalated'
  | 'completed'
  | 'failed'
  | 'cancelled'
  | 'resolved_superseded'
  | 'neutral';

const DOT_COLOR: Record<GroupDot, string> = {
  in_progress: 'text-status-open',
  pending: 'text-status-archiving',
  escalated: 'text-status-escalated',
  completed: 'text-status-open',
  failed: 'text-status-abandoned',
  cancelled: 'text-status-archived',
  resolved_superseded: 'text-status-archived',
  neutral: 'text-text-muted',
};

const STATUS_DOT_KEYS = new Set<string>([
  'in_progress',
  'pending',
  'escalated',
  'completed',
  'failed',
  'cancelled',
  'resolved_superseded',
]);

function groupDot(key: string, by: GroupBy): GroupDot {
  if (by === 'status' && STATUS_DOT_KEYS.has(key)) return key as GroupDot;
  return 'neutral';
}

/**
 * Pasture group section heading — serif display, muted for resolved groups.
 * Carries a colored status dot and a count badge (TASKS-04). Both are pure
 * client-side derivations of the already-loaded roots payload.
 */
function GroupHeading({
  label,
  count,
  dot,
  dimmed,
}: {
  label: string;
  count: number;
  dot: GroupDot;
  dimmed?: boolean;
}): JSX.Element {
  return (
    <h2
      className={`font-display flex items-center gap-2 text-lg font-medium ${
        dimmed ? 'text-text-muted' : 'text-text-primary'
      }`}
    >
      <span
        aria-hidden
        className={`inline-block h-2 w-2 shrink-0 rounded-full bg-current ${DOT_COLOR[dot]}`}
      />
      <span>{label}</span>
      <span className="bg-surface-sunken text-text-muted rounded-full px-1.5 py-0.5 text-xs font-medium tabular-nums">
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
      const threadId = (task as Record<string, unknown>).dispatched_from_thread_id;
      if (threadId && typeof threadId === 'string' && threadId.length > 0) {
        return threadId;
      }
      return 'No thread';
    }
  }
}

function groupLabel(key: string, by: GroupBy): string {
  if (by === 'status') {
    const map: Record<string, string> = {
      pending: 'Pending',
      in_progress: 'In progress',
      escalated: 'Escalated',
      completed: 'Completed',
      failed: 'Failed',
      cancelled: 'Cancelled',
      resolved_superseded: 'Resolved',
    };
    return map[key] ?? key;
  }
  return key;
}

function isResolvedGroup(key: string, by: GroupBy): boolean {
  if (by === 'status') {
    // Terminal/dimmed set. `cancelled` is terminal (muted, calmer than
    // completed); `escalated` is an attention state and stays undimmed.
    return (
      key === 'completed' ||
      key === 'failed' ||
      key === 'cancelled' ||
      key === 'resolved_superseded'
    );
  }
  return false;
}

const GROUP_ORDER_STATUS: Record<string, number> = {
  escalated: 0,
  in_progress: 1,
  pending: 2,
  completed: 3,
  failed: 4,
  cancelled: 5,
  resolved_superseded: 6,
};

export function TasksPage(): JSX.Element {
  const [groupBy, setGroupBy] = useState<GroupBy>('status');
  const routes = useTasksRoutes();
  const tasksQuery = useTasksRootsInfinite();

  const allTasks = useMemo(
    () => tasksQuery.data?.pages.flatMap((p) => p.tasks) ?? [],
    [tasksQuery.data],
  );

  // Page eyebrow — derived ONLY from already-loaded roots-list fields
  // (no extra fetch, no fabrication). "Waiting on you" = roots escalated to
  // the founder (THR-037 Change B: the top-level `escalated` status); "Failed"
  // uses the same severity rollup the rows display. "Subtasks roll up" is a
  // static, honest descriptor of the roots payload (it carries severity_rollup).
  const eyebrow = useMemo(() => {
    const waitingOnYou = allTasks.filter(
      (t) => t.status === 'escalated',
    ).length;
    const failed = allTasks.filter(
      (t) => severityRollupStatus(t) === 'failed',
    ).length;
    return [
      `${allTasks.length} ROOT TASKS`,
      'SUBTASKS ROLL UP',
      `${waitingOnYou} WAITING ON YOU`,
      `${failed} FAILED`,
    ].join(' · ');
  }, [allTasks]);

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

  const isLoading = tasksQuery.isLoading;

  return (
    <div className="bg-surface-canvas flex h-full flex-col">
      {/* Page title + group-by selector */}
      <header className="border-border-default shrink-0 border-b px-6 py-5">
        <p className="text-text-muted text-xs font-medium uppercase tracking-wide">
          {eyebrow}
        </p>
        <h1 className="font-display text-display text-text-primary mt-1 font-medium">
          What the org is working on
        </h1>
        <Tabs
          className="mt-3"
          value={groupBy}
          onValueChange={(v) => setGroupBy(v as GroupBy)}
        >
          <TabsList
            aria-label="Group by"
            className="border-border-default bg-surface-sunken gap-0.5 rounded-lg border p-0.5"
          >
            {GROUP_BY_OPTIONS.map((opt) => (
              <TabsTrigger
                key={opt.value}
                value={opt.value}
                className="data-[state=active]:bg-accent-soft data-[state=active]:text-accent-text rounded-md px-3 py-1"
              >
                {opt.label}
              </TabsTrigger>
            ))}
          </TabsList>
        </Tabs>
      </header>

      {/* Task list */}
      <main className="min-h-0 flex-1 overflow-y-auto px-6 py-4">
        {isLoading ? (
          <p className="text-text-muted py-6 text-center text-sm">Loading…</p>
        ) : allTasks.length === 0 ? (
          <EmptyState title="No tasks" body="No tasks match the current filters." />
        ) : (
          <div className="mx-auto max-w-3xl space-y-6">
            <TaskListColumnHeader />
            {groups.map(([key, tasks]) => {
              const dimmed = isResolvedGroup(key, groupBy);
              return (
                <section key={key} className={dimmed ? 'opacity-60' : undefined}>
                  <GroupHeading
                    label={groupLabel(key, groupBy)}
                    count={tasks.length}
                    dot={groupDot(key, groupBy)}
                    dimmed={dimmed}
                  />
                  <ul className="mt-2">
                    {tasks.map((t) => (
                      <li key={t.task_id}>
                        <TaskListRow
                          task={t}
                          to={routes.detail(t.task_id)}
                          taskRoutes={routes}
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
    </div>
  );
}
