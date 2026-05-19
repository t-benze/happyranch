/**
 * AuditRow — collapsed single-line audit entry that expands inline to
 * reveal the full payload + deep-link buttons. Click the toggle button
 * (or press Enter on it) to expand; click again to collapse.
 *
 * Audit-log entries do not carry agent role, so the actor renders as a
 * plain styled name rather than a role-dotted AgentChip — keeping the
 * design system honest.
 */
import { useState } from 'react';
import { IdBadge } from './IdBadge';
import { cn } from '@/lib/utils';
import type { AuditEntry } from '@/lib/api/audit';

export type Density = 'comfortable' | 'compact';

export interface AuditRowProps {
  entry: AuditEntry;
  density: Density;
  taskHref?: string;
  agentHref?: string;
}

function formatTime(iso: string): string {
  const d = new Date(iso);
  return d.toLocaleString(undefined, {
    month: 'short',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
  });
}

export function AuditRow({
  entry,
  density,
  taskHref,
  agentHref,
}: AuditRowProps): JSX.Element {
  const [open, setOpen] = useState(false);
  const pad = density === 'compact' ? 'py-1' : 'py-2';
  return (
    <li className="border-border-subtle border-b text-sm">
      <button
        type="button"
        aria-label="toggle row"
        aria-expanded={open}
        onClick={() => setOpen((v) => !v)}
        className={cn(
          'hover:bg-surface-raised flex w-full items-center gap-3 px-3 text-left',
          pad,
        )}
      >
        <span className="text-fg-muted font-mono text-xs whitespace-nowrap">
          {formatTime(entry.created_at)}
        </span>
        {entry.agent && <span className="text-fg text-sm">{entry.agent}</span>}
        <span className="text-fg font-mono text-xs">{entry.action}</span>
        {entry.task_id && (
          <IdBadge kind="task" id={entry.task_id} to={taskHref} />
        )}
        <span className="text-fg-muted ml-auto text-xs">{open ? '▾' : '▸'}</span>
      </button>
      {open && (
        <div className="bg-surface-sunken border-border-subtle border-t px-6 py-3">
          <pre className="text-fg overflow-x-auto font-mono text-xs">
{JSON.stringify(entry.payload, null, 2)}
          </pre>
          <div className="mt-3 flex gap-3">
            {taskHref && entry.task_id && (
              <a className="text-accent hover:underline text-xs" href={taskHref}>
                View task →
              </a>
            )}
            {agentHref && entry.agent && (
              <a className="text-accent hover:underline text-xs" href={agentHref}>
                View agent activity →
              </a>
            )}
          </div>
        </div>
      )}
    </li>
  );
}

export const meta = {
  name: 'AuditRow',
  layer: 'pattern',
  import: '@/design-system/patterns/AuditRow',
  variants: { density: ['comfortable', 'compact'] },
  consumes: ['components.audit_row'],
  example: '<AuditRow entry={{} as any} density="compact" />',
} as const;
