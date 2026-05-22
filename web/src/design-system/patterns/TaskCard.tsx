import { Link } from 'react-router-dom';
import { cn } from '@/lib/utils';
import { StatusBadge } from './StatusBadge';
import { IdBadge } from './IdBadge';
import type { TaskRecord } from '@/lib/api/types';

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

export interface TaskCardProps {
  task: TaskRecord;
  to: string;
  active?: boolean;
  density?: Density;
}

export function TaskCard({ task, to, active, density = 'comfortable' }: TaskCardProps): JSX.Element {
  const pad = density === 'compact' ? 'p-2' : 'p-3';
  return (
    <Link
      to={to}
      className={cn(
        'border-border-subtle bg-surface-raised block rounded-lg border',
        pad,
        active && 'ring-accent ring-2',
        'hover:bg-surface-raised/80',
      )}
    >
      <div className="flex items-center gap-2 text-xs">
        <IdBadge kind="task" id={task.task_id} />
        <StatusBadge status={task.status} blockKind={task.block_kind} />
        <span className="text-fg-muted">{task.team}</span>
        {task.assigned_agent && (
          <span className="text-fg-muted">· {task.assigned_agent}</span>
        )}
        <span className="text-fg-muted ml-auto">{relativeAge(task.updated_at)}</span>
      </div>
      <p className="text-fg mt-1 line-clamp-1 text-sm">{briefHeadline(task.brief)}</p>
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
