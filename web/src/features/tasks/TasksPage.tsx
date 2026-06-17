import { useCallback, useMemo, useState } from 'react';
import { useNavigate, useParams } from 'react-router-dom';
import { useTasksRoots, useTasksRoutes } from '@/hooks/tasks';
import { TaskDetailPane } from './TaskDetailPane';
import type { TaskRecord } from '@/lib/api/types';

// ---------------------------------------------------------------------------
// Group-by modes
// ---------------------------------------------------------------------------

type GroupByMode = 'status' | 'agent' | 'thread';

const GROUP_LABELS: Record<GroupByMode, string> = {
  status: 'Status',
  agent: 'Agent',
  thread: 'Thread',
};

// ---------------------------------------------------------------------------
// Severity rollup helpers
// ---------------------------------------------------------------------------

const SEVERITY_LABELS: Record<string, string> = {
  blocked: 'Blocked',
  failed: 'Failed',
  in_progress: 'In progress',
  pending: 'Pending',
  completed: 'Done',
  resolved_superseded: 'Resolved',
};

function severityColor(rollup: string): string {
  switch (rollup) {
    case 'blocked':
      return 'bg-tier-red-tint text-status-abandoned';
    case 'failed':
      return 'bg-tier-amber-tint text-status-failed';
    case 'in_progress':
      return 'bg-tier-blue-tint text-status-active';
    case 'pending':
      return 'bg-tier-slate-tint text-fg-muted';
    case 'completed':
      return 'bg-tier-green-tint text-status-success';
    case 'resolved_superseded':
      return 'bg-tier-slate-tint text-fg-muted';
    default:
      return 'bg-tier-slate-tint text-fg-muted';
  }
}

// ---------------------------------------------------------------------------
// Grouping helpers
// ---------------------------------------------------------------------------

function groupByStatus(tasks: TaskRecord[]): Record<string, TaskRecord[]> {
  const groups: Record<string, TaskRecord[]> = {
    blocked: [],
    failed: [],
    in_progress: [],
    pending: [],
    completed: [],
    resolved_superseded: [],
  };
  for (const t of tasks) {
    const key = t.status;
    if (!groups[key]) groups[key] = [];
    groups[key].push(t);
  }
  return groups;
}

function groupByAgent(tasks: TaskRecord[]): Record<string, TaskRecord[]> {
  const groups: Record<string, TaskRecord[]> = {};
  for (const t of tasks) {
    const key = t.assigned_agent || 'Unassigned';
    if (!groups[key]) groups[key] = [];
    groups[key].push(t);
  }
  return groups;
}

function groupByThread(tasks: TaskRecord[]): Record<string, TaskRecord[]> {
  const groups: Record<string, TaskRecord[]> = {};
  for (const t of tasks) {
    const key = (t as Record<string, unknown>).dispatched_from_thread_id
      ? `Thread ${(t as Record<string, unknown>).dispatched_from_thread_id}`
      : 'No thread';
    if (!groups[key]) groups[key] = [];
    groups[key].push(t);
  }
  return groups;
}

function groupTasks(
  tasks: TaskRecord[],
  mode: GroupByMode,
): Record<string, TaskRecord[]> {
  switch (mode) {
    case 'agent':
      return groupByAgent(tasks);
    case 'thread':
      return groupByThread(tasks);
    default:
      return groupByStatus(tasks);
  }
}

// ---------------------------------------------------------------------------
// Severity rollup pill
// ---------------------------------------------------------------------------

function SeverityPill({ task }: { task: TaskRecord }): JSX.Element {
  const rollup = (task as Record<string, unknown>).severity_rollup as string | undefined;
  const label = SEVERITY_LABELS[rollup ?? task.status] ?? rollup ?? task.status;
  return (
    <span
      className={`inline-flex items-center rounded-full px-2 py-0.5 text-xs font-medium ${severityColor(rollup ?? task.status)}`}
    >
      {label}
    </span>
  );
}

// ---------------------------------------------------------------------------
// Skeleton
// ---------------------------------------------------------------------------

function SkeletonRows({ count = 5 }: { count?: number }): JSX.Element {
  return (
    <div className="space-y-1" aria-busy="true">
      {Array.from({ length: count }).map((_, i) => (
        <div
          key={i}
          className="bg-surface-raised flex h-11 animate-pulse items-center gap-3 rounded px-3"
        >
          <div className="bg-surface-subtle h-4 w-24 rounded" />
          <div className="bg-surface-subtle h-4 flex-1 rounded" />
          <div className="bg-surface-subtle h-5 w-16 rounded-full" />
        </div>
      ))}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Grouped list
// ---------------------------------------------------------------------------

const STATUS_GROUP_ORDER = [
  'blocked', 'failed', 'in_progress', 'pending',
  'completed', 'resolved_superseded',
];

const STATUS_GROUP_LABELS: Record<string, string> = {
  blocked: 'Blocked',
  failed: 'Failed',
  in_progress: 'In review',
  pending: 'Pending',
  completed: 'Done',
  resolved_superseded: 'Resolved (superseded)',
};

function GroupedList({
  groups,
  selectedIdx,
  onSelect,
  onOpen,
}: {
  groups: Record<string, TaskRecord[]>;
  selectedIdx: number;
  onSelect: (idx: number) => void;
  onOpen: (taskId: string) => void;
}): JSX.Element {
  const flat: { task: TaskRecord; groupKey: string }[] = [];
  for (const key of STATUS_GROUP_ORDER) {
    if (!groups[key] || groups[key].length === 0) continue;
    for (const t of groups[key]) {
      flat.push({ task: t, groupKey: key });
    }
  }
  // also add any non-status groups
  for (const [key, tasks] of Object.entries(groups)) {
    if (STATUS_GROUP_ORDER.includes(key)) continue;
    for (const t of tasks) {
      flat.push({ task: t, groupKey: key });
    }
  }

  if (flat.length === 0) {
    return (
      <p className="text-fg-muted py-4 text-center text-sm">Nothing here.</p>
    );
  }

  // Rebuild group boundaries for rendering
  let lastGroup = '';
  const rows: JSX.Element[] = [];
  let idx = 0;
  for (const item of flat) {
    if (item.groupKey !== lastGroup) {
      lastGroup = item.groupKey;
      const groupLabel =
        STATUS_GROUP_LABELS[item.groupKey] ?? item.groupKey;
      rows.push(
        <div
          key={`hdr-${item.groupKey}`}
          className="text-fg-muted sticky top-0 bg-surface-canvas px-3 py-1 text-xs font-medium uppercase tracking-wider"
        >
          {groupLabel}
        </div>,
      );
    }
    const isSelected = idx === selectedIdx;
    const isResolved = item.task.status === 'resolved_superseded';
    const isCurrent = idx === selectedIdx;
    rows.push(
      <div
        key={item.task.task_id}
        role="option"
        aria-selected={isSelected}
        data-task-id={item.task.task_id}
        tabIndex={0}
        className={`flex h-11 cursor-pointer items-center gap-3 rounded px-3 text-sm transition-colors ${
          isCurrent
            ? 'bg-accent-muted ring-accent/30 ring-1'
            : 'hover:bg-surface-raised'
        } ${isResolved ? 'opacity-50' : ''}`}
        onClick={() => onOpen(item.task.task_id)}
        onMouseEnter={() => onSelect(idx)}
        onFocus={() => onSelect(idx)}
      >
        {/* Severity pill */}
        <SeverityPill task={item.task} />
        {/* Brief (truncated) */}
        <span className="min-w-0 flex-1 truncate text-fg">
          {item.task.brief.slice(0, 120)}
        </span>
        {/* Lineage inline */}
        <LineageInline task={item.task} />
        {/* Team / Agent */}
        <span className="text-fg-muted hidden shrink-0 text-xs sm:inline">
          {item.task.team ?? ''}
          {item.task.assigned_agent ? ` · ${item.task.assigned_agent}` : ''}
        </span>
      </div>,
    );
    idx++;
  }

  return (
    <div role="listbox" aria-label="Tasks list" className="space-y-0">
      {rows}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Bidirectional lineage inline
// ---------------------------------------------------------------------------

function LineageInline({ task }: { task: TaskRecord }): JSX.Element | null {
  const revisitOf = task.revisit_of_task_id;
  // Direct revisits come from the roots endpoint if available
  const directRevisits = (task as Record<string, unknown>).direct_revisits as string[] | undefined;

  if (!revisitOf && (!directRevisits || directRevisits.length === 0)) return null;

  return (
    <span className="text-fg-muted shrink-0 text-xs">
      {revisitOf && (
        <span title={`Supersedes ${revisitOf}`}>
          ↳ {revisitOf}
        </span>
      )}
      {revisitOf && directRevisits && directRevisits.length > 0 && ' · '}
      {directRevisits && directRevisits.length > 0 && (
        <span title={`Revisited by ${directRevisits.join(', ')}`}>
          → {directRevisits[0]}
          {directRevisits.length > 1 && ` +${directRevisits.length - 1}`}
        </span>
      )}
    </span>
  );
}

// ---------------------------------------------------------------------------
// Main page
// ---------------------------------------------------------------------------

export function TasksPage(): JSX.Element {
  const { task_id: openTaskId } = useParams<{ task_id: string }>();
  const navigate = useNavigate();
  const routes = useTasksRoutes();

  const [groupBy, setGroupBy] = useState<GroupByMode>('status');
  const [statusFilter, setStatusFilter] = useState<string | undefined>(undefined);
  const [selectedIdx, setSelectedIdx] = useState(-1);

  const { data, isLoading, isError, error } = useTasksRoots(
    statusFilter ? { status: statusFilter } : undefined,
  );

  const allRoots = useMemo(() => data?.tasks ?? [], [data]);
  const groups = useMemo(() => groupTasks(allRoots, groupBy), [allRoots, groupBy]);

  // Flatten groups for keyboard nav
  const flatItems = useMemo(() => {
    const result: TaskRecord[] = [];
    const groupKeys = Object.keys(groups).sort((a, b) => {
      const ai = STATUS_GROUP_ORDER.indexOf(a);
      const bi = STATUS_GROUP_ORDER.indexOf(b);
      if (ai >= 0 && bi >= 0) return ai - bi;
      if (ai >= 0) return -1;
      if (bi >= 0) return 1;
      return a.localeCompare(b);
    });
    for (const key of groupKeys) {
      for (const t of groups[key]) {
        result.push(t);
      }
    }
    return result;
  }, [groups]);

  const openTask = useCallback(
    (taskId: string) => {
      navigate(routes.detail(taskId));
    },
    [navigate, routes],
  );

  const handleKeyDown = useCallback(
    (e: React.KeyboardEvent) => {
      if (e.key === 'ArrowDown') {
        e.preventDefault();
        setSelectedIdx((prev) => Math.min(prev + 1, flatItems.length - 1));
      } else if (e.key === 'ArrowUp') {
        e.preventDefault();
        setSelectedIdx((prev) => Math.max(prev - 1, 0));
      } else if (e.key === 'Enter' && selectedIdx >= 0 && flatItems[selectedIdx]) {
        e.preventDefault();
        openTask(flatItems[selectedIdx].task_id);
      } else if (e.key === 'Escape') {
        e.preventDefault();
        setSelectedIdx(-1);
      }
    },
    [selectedIdx, flatItems, openTask],
  );

  // -------------------------------------------------------------------
  // Status filter tabs (when groupBy === 'status')
  // -------------------------------------------------------------------

  return (
    <div
      className="flex h-full flex-col outline-none"
      onKeyDown={handleKeyDown}
      tabIndex={-1}
    >
      {/* Header: group-by + filter */}
      <header className="border-border-subtle shrink-0 border-b px-4 py-3">
        <div className="flex items-center gap-4">
          {/* Group-by segmented control */}
          <nav role="group" aria-label="Group tasks by" className="flex gap-1">
            {(['status', 'agent', 'thread'] as GroupByMode[]).map((mode) => (
              <button
                key={mode}
                type="button"
                onClick={() => {
                  setGroupBy(mode);
                  setSelectedIdx(-1);
                }}
                className={`rounded px-3 py-1 text-sm font-medium transition-colors ${
                  groupBy === mode
                    ? 'bg-accent text-fg-on-accent'
                    : 'text-fg-muted hover:bg-surface-raised'
                }`}
              >
                {GROUP_LABELS[mode]}
              </button>
            ))}
          </nav>

          {/* Status quick-filters */}
          <div className="flex gap-1">
            {[
              { label: 'All', value: undefined },
              { label: 'Blocked', value: 'blocked' },
              { label: 'Active', value: 'in_progress' },
              { label: 'Done', value: 'completed' },
              { label: 'Resolved', value: 'resolved_superseded' },
            ].map((f) => (
              <button
                key={f.label}
                type="button"
                onClick={() => {
                  setStatusFilter(f.value);
                  setSelectedIdx(-1);
                }}
                className={`rounded px-2 py-1 text-xs transition-colors ${
                  statusFilter === f.value
                    ? 'bg-surface-raised text-fg font-medium'
                    : 'text-fg-muted hover:bg-surface-raised'
                }`}
              >
                {f.label}
              </button>
            ))}
          </div>
        </div>
      </header>

      {/* Body */}
      <main className="min-h-0 flex-1 overflow-y-auto p-4">
        {isLoading ? (
          <SkeletonRows count={6} />
        ) : isError ? (
          <div className="flex flex-col items-center gap-3 py-12 text-center">
            <p className="text-fg-muted text-sm">
              Couldn&apos;t load tasks — {(error as Error)?.message || 'unknown error'}
            </p>
            <p className="text-fg-muted text-xs">
              Check your connection and reload the page to try again.
            </p>
          </div>
        ) : allRoots.length === 0 ? (
          <p className="text-fg-muted py-12 text-center text-sm">
            {statusFilter
              ? `Nothing ${STATUS_GROUP_LABELS[statusFilter]?.toLowerCase() ?? statusFilter}`
              : 'No tasks yet'}
          </p>
        ) : (
          <GroupedList
            groups={groups}
            selectedIdx={selectedIdx}
            onSelect={setSelectedIdx}
            onOpen={openTask}
          />
        )}
      </main>

      {/* Detail pane */}
      {openTaskId && <TaskDetailPane taskId={openTaskId} />}
    </div>
  );
}
