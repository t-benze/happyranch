import { useEffect, useMemo, useRef, useState } from 'react';
import { Link, useParams } from 'react-router-dom';
import { cn } from '@/lib/utils';
import { EmptyState } from '@/design-system/patterns/EmptyState';
import { IdBadge } from '@/design-system/patterns/IdBadge';
import { StatusBadge } from '@/design-system/patterns/StatusBadge';
import { useTasksInfiniteList, useTasksRoutes } from '@/hooks/tasks';
import { TaskDetailPane } from './TaskDetailPane';
import type { TaskRecord, TaskStatus, BlockKind } from '@/lib/api/types';

// ── helpers ──

function relativeAge(iso: string): string {
  const ms = Date.now() - new Date(iso).getTime();
  const min = Math.round(ms / 60000);
  if (min < 1) return 'just now';
  if (min < 60) return `${min}m`;
  const hr = Math.round(min / 60);
  if (hr < 24) return `${hr}h`;
  const d = Math.round(hr / 24);
  return `${d}d`;
}

function briefHeadline(brief: string): string {
  const line = brief.split('\n').find((l) => l.trim().length > 0) ?? '';
  return line.trim().replace(/^#+\s*/, '');
}

// ── group‑by ──

type GroupKey = 'status' | 'agent' | 'thread';

const GROUP_LABELS: Record<GroupKey, string> = {
  status: 'Status',
  agent: 'Agent',
  thread: 'Thread',
};

function groupKeyLabel(key: GroupKey, value: string): string {
  if (key === 'status') {
    if (value === 'resolved_superseded') return 'Resolved (superseded)';
    return value.replace(/_/g, ' ');
  }
  return value;
}

// ── supersede badge ──

function SupersedeBadge({ task }: { task: TaskRecord }): JSX.Element | null {
  const routes = useTasksRoutes();
  const revisits = (task as Record<string, unknown>).direct_revisits as string[] | undefined;
  const hasBackPointer = !!task.revisit_of_task_id;
  if (!revisits?.length && !hasBackPointer) return null;
  return (
    <span className="text-fg-muted ml-1 inline-flex gap-1 text-[0.65rem] font-mono">
      {task.revisit_of_task_id && (
        <Link
          to={routes.detail(task.revisit_of_task_id)}
          className="text-accent hover:underline"
        >
          ↳ {task.revisit_of_task_id}
        </Link>
      )}
      {revisits?.map((rid) => (
        <Link
          key={rid}
          to={routes.detail(rid)}
          className="text-accent hover:underline"
        >
          → {rid}
        </Link>
      ))}
    </span>
  );
}

// ── root status (with rollup) ──

function worstStatus(a: TaskStatus, b: TaskStatus): TaskStatus {
  const order: TaskStatus[] = ['failed', 'blocked', 'in_progress', 'pending', 'completed', 'resolved_superseded'];
  const aIdx = order.indexOf(a);
  const bIdx = order.indexOf(b);
  return aIdx < bIdx ? a : b;
}

function effectiveStatus(task: TaskRecord): TaskStatus {
  const wcs = task.worst_child_status;
  if (wcs) {
    return worstStatus(task.status as TaskStatus, wcs);
  }
  return task.status as TaskStatus;
}

// ── component ──

export function TasksPage(): JSX.Element {
  const { task_id: openTaskId } = useParams<{ task_id: string }>();
  const routes = useTasksRoutes();

  const [groupBy, setGroupBy] = useState<GroupKey>('status');
  const tasksQuery = useTasksInfiniteList({ roots_only: true });

  const allTasks = useMemo(
    () => tasksQuery.data?.pages.flatMap((p) => p.tasks) ?? [],
    [tasksQuery.data],
  );

  const groups = useMemo(() => {
    const map = new Map<string, TaskRecord[]>();
    for (const t of allTasks) {
      let key: string;
      switch (groupBy) {
        case 'agent':
          key = t.assigned_agent || 'unassigned';
          break;
        case 'thread':
          key = (t as Record<string, unknown>).dispatched_from_thread_id as string || 'no thread';
          break;
        case 'status':
        default:
          key = effectiveStatus(t);
          break;
      }
      const bucket = map.get(key) || [];
      bucket.push(t);
      map.set(key, bucket);
    }
    // Order groups: most severe first for status, alpha for others.
    const entries = [...map.entries()];
    if (groupBy === 'status') {
      const order: TaskStatus[] = ['failed', 'blocked', 'in_progress', 'pending', 'completed', 'resolved_superseded'];
      entries.sort(([a], [b]) => {
        const aIdx = order.indexOf(a as TaskStatus);
        const bIdx = order.indexOf(b as TaskStatus);
        return (aIdx === -1 ? 99 : aIdx) - (bIdx === -1 ? 99 : bIdx);
      });
    } else {
      entries.sort(([a], [b]) => a.localeCompare(b));
    }
    return entries;
  }, [allTasks, groupBy]);

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

  const GROUP_KEYS: GroupKey[] = ['status', 'agent', 'thread'];

  return (
    <div className="flex h-full">
      {/* group‑by sidebar */}
      <aside className="border-border-subtle bg-surface-sunken w-52 shrink-0 overflow-y-auto border-r p-3">
        <h3 className="text-fg-muted mb-2 text-xs font-medium tracking-wider uppercase">Group by</h3>
        <ul className="space-y-0.5">
          {GROUP_KEYS.map((key) => (
            <li key={key}>
              <button
                type="button"
                onClick={() => setGroupBy(key)}
                className={cn(
                  'w-full rounded px-2 py-1 text-left text-sm',
                  groupBy === key
                    ? 'bg-accent-muted text-fg'
                    : 'text-fg-muted hover:bg-surface-raised',
                )}
              >
                {GROUP_LABELS[key]}
              </button>
            </li>
          ))}
        </ul>
      </aside>

      {/* list */}
      <main className="bg-surface-canvas flex-1 overflow-y-auto">
        {tasksQuery.isLoading ? (
          <p className="text-fg-muted p-6 text-center text-sm">Loading…</p>
        ) : allTasks.length === 0 ? (
          <div className="p-6">
            <EmptyState
              title="No tasks"
              body="No tasks yet. Dispatch one from a thread or the CLI."
            />
          </div>
        ) : (
          <>
            {groups.map(([groupValue, groupTasks]) => {
              const label = groupKeyLabel(groupBy, groupValue);
              const isSuperseded =
                groupBy === 'status' && (groupValue === 'resolved_superseded');
              return (
                <section key={groupValue}>
                  <h3
                    className={cn(
                      'border-border-subtle sticky top-0 z-10 border-b px-4 py-2 text-xs font-semibold tracking-wider uppercase',
                      isSuperseded
                        ? 'bg-surface-sunken text-fg-subtle'
                        : 'bg-surface-canvas text-fg-muted',
                    )}
                  >
                    {label}
                    <span className="text-fg-subtle ml-2 font-mono">{groupTasks.length}</span>
                  </h3>
                  <ul className={isSuperseded ? 'opacity-60' : undefined}>
                    {groupTasks.map((task) => (
                      <li
                        key={task.task_id}
                        className={cn(
                          'border-border-subtle border-b',
                          openTaskId === task.task_id && 'bg-accent-muted',
                          'hover:bg-surface-raised',
                        )}
                      >
                        <Link
                          to={routes.detail(task.task_id)}
                          className="flex items-center gap-2 px-4 py-2 text-sm"
                        >
                          <IdBadge kind="task" id={task.task_id} />
                          <StatusBadge
                            status={effectiveStatus(task)}
                            blockKind={task.block_kind as BlockKind | null}
                          />
                          <span className="text-fg flex-1 line-clamp-1">
                            {briefHeadline(task.brief)}
                          </span>
                          <SupersedeBadge task={task} />
                          {task.assigned_agent && (
                            <span className="text-fg-muted hidden text-xs sm:inline">
                              {task.assigned_agent}
                            </span>
                          )}
                          <span className="text-fg-subtle text-xs font-mono shrink-0">
                            {relativeAge(task.updated_at)}
                          </span>
                        </Link>
                      </li>
                    ))}
                  </ul>
                </section>
              );
            })}
            <div ref={sentinelRef} aria-hidden className="h-1" />
            {isFetchingNextPage && (
              <p className="text-fg-muted py-3 text-center text-sm">Loading more…</p>
            )}
            {!hasNextPage && allTasks.length > 0 && (
              <p className="text-fg-subtle py-3 text-center text-xs">End of list</p>
            )}
          </>
        )}
      </main>
      {openTaskId && <TaskDetailPane taskId={openTaskId} />}
    </div>
  );
}
