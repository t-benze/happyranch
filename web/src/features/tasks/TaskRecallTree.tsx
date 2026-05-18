import type { TaskRecallNode } from '@/lib/api/types';
import { IdBadge } from '@/design-system/patterns/IdBadge';
import { StatusBadge } from '@/design-system/patterns/StatusBadge';
import { useTasksRoutes } from '@/hooks/tasks';

export function TaskRecallTree({ node, depth = 0 }: { node: TaskRecallNode; depth?: number }): JSX.Element {
  const routes = useTasksRoutes();
  return (
    <div style={{ paddingLeft: depth * 16 }} className="py-1">
      <div className="flex items-center gap-2 text-sm">
        <IdBadge kind="task" id={node.task_id} to={routes.detail(node.task_id)} />
        <span className="text-fg-muted">{node.team}</span>
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
