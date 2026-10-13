import { Link } from 'react-router-dom';
import { cn } from '@/lib/utils';
import { StatusBadge } from './StatusBadge';
import { IdBadge } from './IdBadge';
import type { TaskRecord, TaskStatus } from '@/lib/api/types';
import type { ReactNode } from 'react';

// Inline the union rather than importing `Density` from `@/hooks/` —
// patterns must not reach into hooks (per `ARCHITECTURE.md`). The
// `useDensity` hook is still the runtime owner; this pattern just
// receives the value as a prop.
type Density = 'comfortable' | 'compact';

function relativeAge(iso: string, labels?: TaskCardProps['labels']): string {
  const ms = Date.now() - new Date(iso).getTime();
  const min = Math.round(ms / 60000);
  if (min < 1) return labels?.age.justNow ?? 'just now';
  if (min < 60) return labels?.age.minutes(min) ?? `${min}m`;
  const hr = Math.round(min / 60);
  if (hr < 24) return labels?.age.hours(hr) ?? `${hr}h`;
  const d = Math.round(hr / 24);
  return labels?.age.days(d) ?? `${d}d`;
}

// Briefs are markdown — often a multi-page document with headings, code
// fences, and PR details. The list view only needs a scannable one-liner,
// so pick the first non-empty line and strip leading heading markers.
function briefHeadline(brief: string): string {
  const line = brief.split('\n').find((l) => l.trim().length > 0) ?? '';
  return line.trim().replace(/^#+\s*/, '');
}

/** The /tasks/roots endpoint includes severity_rollup — the worst status
 *  across the root's subtree. Fall back to the root's own status when the
 *  field is absent (e.g. non-roots endpoints). */
function severityRollupStatus(task: TaskRecord): TaskStatus {
  const r = (task as Record<string, unknown>).severity_rollup;
  if (typeof r === 'string' && r.length > 0) return r as TaskStatus;
  return task.status;
}

/** Read the direct_revisits list (array of task_id strings) from the
 *  roots-payload extra fields. */
function directRevisits(task: TaskRecord): string[] {
  const r = (task as Record<string, unknown>).direct_revisits;
  if (Array.isArray(r)) return r.filter((v): v is string => typeof v === 'string');
  return [];
}

/** Shape of the route helper injected by feature callers so TaskCard
 *  stays a pure pattern (props in, JSX out) with no hook imports. */
export interface TaskCardRoutes {
  detail(taskId: string): string;
}

export interface TaskCardProps {
  task: TaskRecord;
  agentLabel?: ReactNode;
  to: string;
  active?: boolean;
  density?: Density;
  /** Injected by the feature layer so the pattern doesn't import useTasksRoutes. */
  taskRoutes?: TaskCardRoutes;
  /** Feature owners supply presentation only; omitted props retain standalone defaults. */
  labels?: {
    age: {
      justNow: string;
      minutes(count: number): string;
      hours(count: number): string;
      days(count: number): string;
    };
    supersedes(id: ReactNode): ReactNode;
    supersededBy(id: ReactNode): ReactNode;
    waiting: Record<'delegated' | 'blocked_on_job', string>;
  };
}

/** Direction-A Pasture task card — ds.css .card (bg-surface, rounded-lg 18px, soft shadow). */
export function TaskCard({ task, agentLabel, to, active, density = 'comfortable', taskRoutes, labels }: TaskCardProps): JSX.Element {
  const pad = density === 'compact' ? 'px-3 py-2' : 'px-4 py-3';
  const rollup = severityRollupStatus(task);
  const revisits = directRevisits(task);

  return (
    <div
      className={cn(
        'border-border-default bg-surface shadow-pasture-sm rounded-lg border',
        pad,
        active && 'ring-accent-default ring-2',
      )}
    >
      <Link
        to={to}
        className="block hover:bg-surface-hover transition-colors rounded-lg"
      >
        {/* Narrow rows wrap whole groups; the ID, status pill and age keep their own width. */}
        <div className="flex flex-wrap items-center gap-x-2 gap-y-1 text-xs">
          <span className="flex shrink-0"><IdBadge kind="task" id={task.task_id} /></span>
          <span className="flex shrink-0"><StatusBadge status={rollup} blockKind={task.block_kind} waitingLabels={labels?.waiting} /></span>
          <span className="text-text-muted font-mono text-xs tabular-nums">{task.team}</span>
          {task.assigned_agent && (
            <span className="text-text-muted">· {agentLabel ?? task.assigned_agent}</span>
          )}
          <span className="text-text-muted ml-auto shrink-0 text-xs tabular-nums">{relativeAge(task.updated_at, labels)}</span>
        </div>
        <p className="text-text-primary mt-1 line-clamp-1 text-sm">{briefHeadline(task.brief)}</p>
      </Link>

      {/* Supersede / revisit links — from roots-payload fields, siblings of the main Link */}
      {taskRoutes && (task.revisit_of_task_id || revisits.length > 0) && (
        <div className="text-text-muted mt-1.5 flex flex-wrap gap-x-3 gap-y-0.5 text-xs">
          {task.revisit_of_task_id && (
            <Link
              to={taskRoutes.detail(task.revisit_of_task_id)}
              className="hover:underline"
            >
              {labels
                ? labels.supersedes(<span className="font-mono text-id-task">{task.revisit_of_task_id}</span>)
                : <>supersedes{' '}<span className="font-mono text-id-task">{task.revisit_of_task_id}</span></>}
            </Link>
          )}
          {revisits.map((rid) => (
            <Link
              key={rid}
              to={taskRoutes.detail(rid)}
              className="hover:underline"
            >
              {labels
                ? labels.supersededBy(<span className="font-mono text-id-task">{rid}</span>)
                : <>superseded by{' '}<span className="font-mono text-id-task">{rid}</span></>}
            </Link>
          ))}
        </div>
      )}
    </div>
  );
}
