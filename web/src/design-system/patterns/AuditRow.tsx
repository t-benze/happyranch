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
import { Link } from 'react-router-dom';
import { IdBadge } from './IdBadge';
import { cn } from '@/lib/utils';
import type { AuditEntry } from '@/lib/api/types';

export type Density = 'comfortable' | 'compact';

export interface AuditRowProps {
  entry: AuditEntry;
  density: Density;
  taskHref?: string;
  agentHref?: string;
  /** Base path to the SR drawer, e.g. `/orgs/slug/scripts`. Provided by
   *  the parent when the entry action is a `script_*` action. */
  scriptsBasePath?: string;
}

/** Render a human-readable one-liner for script_* audit actions.
 *  Returns null when the action is not a script action. */
function ScriptActionSummary({
  entry,
  scriptsBasePath,
}: {
  entry: AuditEntry;
  scriptsBasePath?: string;
}): JSX.Element | null {
  const { action, payload } = entry;
  if (!action.startsWith('script_')) return null;

  const srId = payload.script_request_id as string | undefined;
  const title = payload.title as string | undefined;
  const reason = payload.reason as string | undefined;
  const exitCode = payload.exit_code as number | undefined;
  const durationMs = payload.duration_ms as number | undefined;

  const srLink =
    srId && scriptsBasePath ? (
      <Link
        to={`${scriptsBasePath}/${srId}`}
        className="text-accent hover:underline font-mono"
        onClick={(e) => e.stopPropagation()}
      >
        {srId}
      </Link>
    ) : (
      <span className="font-mono">{srId ?? '?'}</span>
    );

  switch (action) {
    case 'script_submitted':
      return (
        <span>
          submitted {srLink}{title ? `: ${title}` : ''}
        </span>
      );
    case 'script_rejected':
      return (
        <span>
          rejected {srLink}{reason ? ` — ${reason}` : ''}
        </span>
      );
    case 'script_run_started':
      return <span>started running {srLink}</span>;
    case 'script_run_completed':
      return (
        <span>
          completed {srLink}
          {(exitCode !== undefined || durationMs !== undefined) && (
            <> (exit={exitCode ?? '?'}, {durationMs ?? '?'}ms)</>
          )}
        </span>
      );
    case 'script_run_failed':
      return (
        <span>
          failed {srLink}{reason ? `: ${reason}` : ''}
        </span>
      );
    default:
      return null;
  }
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
  scriptsBasePath,
}: AuditRowProps): JSX.Element {
  const [open, setOpen] = useState(false);
  const pad = density === 'compact' ? 'py-1' : 'py-2';
  const isScriptAction = entry.action.startsWith('script_');
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
        {isScriptAction ? (
          <span className="text-fg text-xs">
            <ScriptActionSummary entry={entry} scriptsBasePath={scriptsBasePath} />
          </span>
        ) : (
          <span className="text-fg font-mono text-xs">{entry.action}</span>
        )}
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
              <a className="text-accent text-xs hover:underline" href={taskHref}>
                View task →
              </a>
            )}
            {agentHref && entry.agent && (
              <a className="text-accent text-xs hover:underline" href={agentHref}>
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
