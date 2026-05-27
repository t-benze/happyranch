import { useState } from 'react';
import type { TaskRecallNode } from '@/lib/api/types';
import { IdBadge } from '@/design-system/patterns/IdBadge';
import { StatusBadge } from '@/design-system/patterns/StatusBadge';
import { Markdown } from '@/design-system/patterns/Markdown';
import { useTasksRoutes } from '@/hooks/tasks';

// Depth-to-Tailwind padding map. Tailwind needs static class names at build
// time, so we cannot interpolate `pl-[${depth*16}px]`. Clamp at 10 levels —
// deeper recall trees indent at the same step as level 10.
const DEPTH_PL = [
  'pl-0', 'pl-4', 'pl-8', 'pl-12', 'pl-16',
  'pl-20', 'pl-24', 'pl-28', 'pl-32', 'pl-36', 'pl-40',
] as const;

const COLLAPSE_THRESHOLD = 240;

function CollapsibleBody({
  body,
  label,
  muted = false,
}: {
  body: string;
  label: string;
  muted?: boolean;
}): JSX.Element {
  const [expanded, setExpanded] = useState(false);
  const shouldCollapse = body.length > COLLAPSE_THRESHOLD;
  const shown = shouldCollapse && !expanded
    ? body.slice(0, COLLAPSE_THRESHOLD).replace(/\s+\S*$/, '') + '…'
    : body;
  return (
    <div className={muted ? 'text-fg-muted text-xs' : 'text-fg text-sm'}>
      <Markdown body={shown} />
      {shouldCollapse && (
        <button
          type="button"
          onClick={() => setExpanded((v) => !v)}
          className="text-accent mt-1 text-xs hover:underline"
        >
          {expanded ? `Hide ${label}` : `Show full ${label} (${body.length} chars)`}
        </button>
      )}
    </div>
  );
}

export function TaskRecallTree({ node, depth = 0 }: { node: TaskRecallNode; depth?: number }): JSX.Element {
  const routes = useTasksRoutes();
  const pl = DEPTH_PL[Math.min(depth, DEPTH_PL.length - 1)];
  return (
    <div className={`${pl} py-2`}>
      <div className="flex items-center gap-2 text-sm">
        <IdBadge kind="task" id={node.task_id} to={routes.detail(node.task_id)} />
        {node.assigned_agent && (
          <span className="text-fg-muted">{node.assigned_agent}</span>
        )}
        <StatusBadge status={node.status} />
      </div>
      {node.brief && (
        <div className="mt-1">
          <CollapsibleBody body={node.brief} label="brief" />
        </div>
      )}
      {node.output_summary && (
        <div className="mt-2 border-l-2 border-border-subtle pl-3">
          <div className="text-fg-muted text-[10px] font-medium tracking-wider uppercase">
            Outcome
          </div>
          <CollapsibleBody body={node.output_summary} label="summary" muted />
        </div>
      )}
      {node.children.map((c) => (
        <TaskRecallTree key={c.task_id} node={c} depth={depth + 1} />
      ))}
    </div>
  );
}
