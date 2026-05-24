import { useState } from 'react';
import { Link, useParams } from 'react-router-dom';
import { FilterSidebar, type FilterGroup } from '@/design-system/patterns/FilterSidebar';
import { EmptyState } from '@/design-system/patterns/EmptyState';
import { cn } from '@/lib/utils';
import { useScriptsList, useScriptsRoutes } from '@/hooks/scripts';
import type { ScriptRequest, ScriptRequestStatus } from '@/lib/api/types';
import { ScriptDetailPane } from './ScriptDetailPane';

const STATUSES: FilterGroup['options'] = [
  { value: 'pending', label: 'Pending' },
  { value: 'running', label: 'Running' },
  { value: 'completed', label: 'Completed' },
  { value: 'failed', label: 'Failed' },
  { value: 'rejected', label: 'Rejected' },
];

// Script-specific status colours (extends design-system tokens where possible)
const STATUS_CLASS: Record<ScriptRequestStatus, string> = {
  pending: 'bg-tier-yellow-tint text-status-archiving',
  running: 'bg-tier-green-tint text-status-open',
  completed: 'border border-border-subtle bg-transparent text-status-archived',
  failed: 'bg-tier-red-tint text-status-abandoned',
  rejected: 'border border-border-subtle bg-transparent text-fg-muted',
};

function ScriptStatusBadge({ status }: { status: ScriptRequestStatus }): JSX.Element {
  return (
    <span
      className={cn(
        'text-mono-sm inline-flex items-center rounded-sm px-2 py-px font-mono text-xs font-semibold',
        STATUS_CLASS[status],
      )}
    >
      {status}
    </span>
  );
}

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

interface ScriptCardProps {
  script: ScriptRequest;
  to: string;
  active?: boolean;
}

function ScriptCard({ script, to, active }: ScriptCardProps): JSX.Element {
  return (
    <Link
      to={to}
      className={cn(
        'border-border-subtle bg-surface-raised block rounded-lg border p-3',
        active && 'ring-accent ring-2',
        'hover:bg-surface-raised/80',
      )}
    >
      <div className="flex items-center gap-2 text-xs">
        <span className="text-id-task font-mono">{script.id}</span>
        <ScriptStatusBadge status={script.status} />
        <span className="text-fg-muted">{script.agent_name}</span>
        <span className="text-fg-muted">· {script.task_id}</span>
        <span className="text-fg-muted ml-auto">{relativeAge(script.created_at)}</span>
      </div>
      <p className="text-fg mt-1 line-clamp-1 text-sm font-medium">{script.title}</p>
      {script.rationale && (
        <p className="text-fg-muted mt-0.5 line-clamp-2 text-xs">{script.rationale}</p>
      )}
    </Link>
  );
}

export function ScriptsPage(): JSX.Element {
  const { sr_id: openSrId } = useParams<{ sr_id: string }>();
  const [filters, setFilters] = useState<Record<string, string | null>>({
    status: null,
  });
  const routes = useScriptsRoutes();
  const scriptsQuery = useScriptsList(
    filters.status ? { status: filters.status } : undefined,
  );

  const scripts = scriptsQuery.data?.scripts ?? [];

  const groups: FilterGroup[] = [
    { key: 'status', label: 'Status', options: STATUSES },
  ];

  return (
    <div className="flex h-full">
      <FilterSidebar groups={groups} value={filters} onChange={setFilters} />
      <main className="bg-surface-canvas flex-1 overflow-y-auto p-4">
        {scriptsQuery.isLoading ? (
          <p className="text-fg-muted">Loading…</p>
        ) : scripts.length === 0 ? (
          <EmptyState
            title="No script requests"
            body="Script requests submitted by agents will appear here."
          />
        ) : (
          <ul className="space-y-2">
            {scripts.map((sr) => (
              <li key={sr.id}>
                <ScriptCard
                  script={sr}
                  to={routes.detail(sr.id)}
                  active={openSrId === sr.id}
                />
              </li>
            ))}
          </ul>
        )}
      </main>
      {openSrId && <ScriptDetailPane srId={openSrId} />}
    </div>
  );
}
