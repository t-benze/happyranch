import { Link } from 'react-router-dom';
import { cn } from '@/lib/utils';
import { StatusBadge } from './StatusBadge';
import { IdBadge } from './IdBadge';
import type { TaskRecord, TaskStatus } from '@/lib/api/types';

// Inline the union rather than importing `Density` from `@/hooks/` —
// patterns must not reach into hooks (per `ARCHITECTURE.md`). The
// `useDensity` hook is still the runtime owner; this pattern just
// receives the value as a prop.
type Density = 'comfortable' | 'compact';

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

export interface TaskCardProps {
  task: TaskRecord;
  to: string;
  active?: boolean;
  density?: Density;
}

/** Direction-A Pasture task card — ds.css .card (bg-surface, rounded-lg 18px, soft shadow). */
export function TaskCard({ task, to, active, density = 'comfortable' }: TaskCardProps): JSX.Element {
  const pad = density === 'compact' ? 'px-3 py-2' : 'px-4 py-3';
  const rollup = severityRollupStatus(task);
  const revisits = directRevisits(task);

  return (
    <Link
      to={to}
      className={cn(
        'border-border-default bg-surface shadow-pasture-sm block rounded-lg border',
        pad,
        active && 'ring-accent-default ring-2',
        'hover:bg-surface-hover transition-colors',
      )}
    >
      <div className="flex items-center gap-2 text-xs">
        <IdBadge kind="task" id={task.task_id} />
        <StatusBadge status={rollup} blockKind={task.block_kind} />
        <span className="text-text-muted font-mono text-xs tabular-nums">{task.team}</span>
        {task.assigned_agent && (
          <span className="text-text-muted">· {task.assigned_agent}</span>
        )}
        <span className="text-text-muted ml-auto text-xs tabular-nums">{relativeAge(task.updated_at)}</span>
      </div>
      <p className="text-text-primary mt-1 line-clamp-1 text-sm">{briefHeadline(task.brief)}</p>

      {/* Supersede / revisit links — from roots-payload fields */}
      {(task.revisit_of_task_id || revisits.length > 0) && (
        <div className="text-text-muted mt-1.5 flex flex-wrap gap-x-3 gap-y-0.5 text-xs">
          {task.revisit_of_task_id && (
            <span>
              supersedes{' '}
              <span className="font-mono text-id-task">{task.revisit_of_task_id}</span>
            </span>
          )}
          {revisits.map((rid) => (
            <span key={rid}>
              superseded by{' '}
              <span className="font-mono text-id-task">{rid}</span>
            </span>
          ))}
        </div>
      )}
    </Link>
  );
}

export const meta = {
  name: "TaskCard",
  layer: "pattern",
  import: "@/design-system/patterns/TaskCard",
  variants: {},
  consumes: ["components.badge"],
  example: "<TaskCard task={task} to='/orgs/x/tasks/TASK-001' />",
} as const;
