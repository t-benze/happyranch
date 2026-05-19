/**
 * TraceTree — recursive recall renderer with right-edge cost annotation.
 * Cost data is supplied as a flat `task_id -> { tokens, usd? }` map; the
 * parent computes it once per page-view from the audit list (typically by
 * projecting session_end payloads).
 */
import type { TaskRecallNode } from '@/lib/api/types';
import { IdBadge } from './IdBadge';
import { StatusBadge } from './StatusBadge';
import type { Density } from './AuditRow';

const DEPTH_PL = [
  'pl-0', 'pl-4', 'pl-8', 'pl-12', 'pl-16',
  'pl-20', 'pl-24', 'pl-28', 'pl-32', 'pl-36', 'pl-40',
] as const;

export interface CostCell {
  tokens: number;
  usd?: number;
}

export interface TraceTreeProps {
  root: TaskRecallNode;
  costs: Record<string, CostCell>;
  density: Density;
  taskHref?: (taskId: string) => string;
}

function fmtTokens(n: number): string {
  return n.toLocaleString();
}

function fmtUsd(n: number): string {
  return `$${n.toFixed(2)}`;
}

export function TraceTree({
  root,
  costs,
  density,
  taskHref,
}: TraceTreeProps): JSX.Element {
  return (
    <Node
      node={root}
      depth={0}
      costs={costs}
      density={density}
      taskHref={taskHref}
    />
  );
}

function Node({
  node,
  depth,
  costs,
  density,
  taskHref,
}: {
  node: TaskRecallNode;
  depth: number;
  costs: Record<string, CostCell>;
  density: Density;
  taskHref?: (taskId: string) => string;
}): JSX.Element {
  const pl = DEPTH_PL[Math.min(depth, DEPTH_PL.length - 1)];
  const pad = density === 'compact' ? 'py-0.5' : 'py-1';
  const cost = costs[node.task_id];
  return (
    <div className={`${pl} ${pad}`}>
      <div className="flex items-center gap-2 text-sm">
        <IdBadge
          kind="task"
          id={node.task_id}
          to={taskHref ? taskHref(node.task_id) : undefined}
        />
        {node.assigned_agent && (
          <span className="text-fg-muted">{node.assigned_agent}</span>
        )}
        <StatusBadge status={node.status} />
        {cost && (
          <span className="text-fg-muted ml-auto font-mono text-xs">
            {fmtTokens(cost.tokens)} tok
            {cost.usd != null ? ` · ${fmtUsd(cost.usd)}` : ''}
          </span>
        )}
      </div>
      <p className="text-fg mt-1 text-sm">{node.brief}</p>
      {node.children.map((c) => (
        <Node
          key={c.task_id}
          node={c}
          depth={depth + 1}
          costs={costs}
          density={density}
          taskHref={taskHref}
        />
      ))}
    </div>
  );
}

export const meta = {
  name: 'TraceTree',
  layer: 'pattern',
  import: '@/design-system/patterns/TraceTree',
  variants: { density: ['comfortable', 'compact'] },
  consumes: ['components.trace_tree'],
  example: '<TraceTree root={{} as any} costs={{}} density="compact" />',
} as const;
