import type { TaskRecallNode } from '@/lib/api/types';
import { IdBadge } from '@/design-system/patterns/IdBadge';
import { StatusBadge } from '@/design-system/patterns/StatusBadge';
import { useTasksRoutes } from '@/hooks/tasks';

// Depth-to-Tailwind padding map. Tailwind needs static class names at build
// time, so we cannot interpolate `pl-[${depth*16}px]`. Clamp at 10 levels —
// deeper recall trees indent at the same step as level 10.
const DEPTH_PL = [
  'pl-0', 'pl-4', 'pl-8', 'pl-12', 'pl-16',
  'pl-20', 'pl-24', 'pl-28', 'pl-32', 'pl-36', 'pl-40',
] as const;

export function TaskRecallTree({ node, depth = 0 }: { node: TaskRecallNode; depth?: number }): JSX.Element {
  const routes = useTasksRoutes();
  const pl = DEPTH_PL[Math.min(depth, DEPTH_PL.length - 1)];
  return (
    <div className={`${pl} py-1`}>
      <div className="flex items-center gap-2 text-sm">
        <IdBadge kind="task" id={node.task_id} to={routes.detail(node.task_id)} />
        {node.assigned_agent && (
          <span className="text-fg-muted">{node.assigned_agent}</span>
        )}
        <StatusBadge status={node.status} />
      </div>
      <p className="text-fg mt-1 text-sm">{node.brief}</p>
      {node.output_summary && (
        <p className="text-fg-muted mt-1 text-xs italic">{node.output_summary}</p>
      )}
      {node.children.map((c) => (
        <TaskRecallTree key={c.task_id} node={c} depth={depth + 1} />
      ))}
    </div>
  );
}
