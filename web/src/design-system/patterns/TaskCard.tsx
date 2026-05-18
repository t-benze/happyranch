import { Link } from 'react-router-dom';
import { cn } from '@/lib/utils';
import { StatusBadge } from './StatusBadge';
import { IdBadge } from './IdBadge';
import type { TaskRecord } from '@/lib/api/types';
import type { Density } from '@/hooks/density';

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
      <div className="flex items-center gap-2">
        <IdBadge kind="task" id={task.task_id} />
        <span className="text-fg-muted text-xs">{task.team}</span>
      </div>
      <p className="text-fg mt-1 text-sm">{task.brief}</p>
      <div className="text-fg-muted mt-2 flex items-center gap-2 text-xs">
        <StatusBadge status={task.status} blockKind={task.block_kind} />
        <span>· updated {relativeAge(task.updated_at)} ago</span>
      </div>
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
