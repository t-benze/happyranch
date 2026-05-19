import { useMemo } from 'react';
import { useParams, useSearchParams } from 'react-router-dom';
import { IdBadge } from '@/design-system/patterns/IdBadge';
import { EmptyState } from '@/design-system/patterns/EmptyState';
import { useAuditList } from '@/hooks/audit';
import type { AuditEntry } from '@/lib/api/types';
import { decodeFilters, sinceToISO } from './audit-filters';

interface Folded {
  raised_at: string;
  resolved_at: string | null;
  agent: string | null;
  task_id: string | null;
}

function fold(entries: AuditEntry[]): Folded[] {
  const resolved = new Map<string, string>();
  for (const e of entries) {
    if (e.action === 'escalation_resolved' && e.task_id) {
      resolved.set(e.task_id, e.created_at);
    }
  }
  return entries
    .filter((e) => e.action === 'escalation')
    .map((e) => ({
      raised_at: e.created_at,
      resolved_at: e.task_id ? (resolved.get(e.task_id) ?? null) : null,
      agent: e.agent,
      task_id: e.task_id,
    }));
}

function delta(raised: string, resolved: string | null): string {
  if (!resolved) return '—';
  const ms = new Date(resolved).getTime() - new Date(raised).getTime();
  const mins = Math.round(ms / 60_000);
  if (mins < 60) return `${mins}m`;
  const hrs = Math.floor(mins / 60);
  const rem = mins % 60;
  return rem ? `${hrs}h ${rem}m` : `${hrs}h`;
}

export function EscalationsTab(): JSX.Element {
  const { slug } = useParams<{ slug: string }>();
  const [searchParams] = useSearchParams();
  const filters = useMemo(() => decodeFilters(searchParams), [searchParams]);
  // Pull a wider window than Activity so we can fold raised/resolved pairs
  // client-side without two round-trips.
  const auditQuery = useAuditList({
    agent: filters.agent,
    since: sinceToISO(filters.since),
    limit: 500,
  });

  if (auditQuery.isLoading) return <p className="text-fg-muted">Loading…</p>;
  const entries = auditQuery.data?.entries ?? [];
  const folded = fold(entries).sort((a, b) =>
    a.raised_at < b.raised_at ? 1 : -1,
  );
  if (folded.length === 0) {
    return (
      <EmptyState
        title="No escalations"
        body="No escalations match the current filters."
      />
    );
  }
  return (
    <table className="w-full text-sm">
      <thead className="text-fg-muted border-border-subtle border-b text-left">
        <tr>
          <th className="px-3 py-2">Raised</th>
          <th className="px-3 py-2">Agent</th>
          <th className="px-3 py-2">Task</th>
          <th className="px-3 py-2">Status</th>
          <th className="px-3 py-2 text-right">Δ to resolve</th>
        </tr>
      </thead>
      <tbody>
        {folded.map((row, i) => (
          <tr
            key={`${row.task_id ?? 'no-task'}-${i}`}
            className="border-border-subtle border-b"
          >
            <td className="text-fg-muted px-3 py-2 font-mono text-xs">
              {new Date(row.raised_at).toLocaleString()}
            </td>
            <td className="text-fg px-3 py-2">{row.agent ?? '—'}</td>
            <td className="px-3 py-2">
              {row.task_id && (
                <IdBadge
                  kind="task"
                  id={row.task_id}
                  to={`/orgs/${slug ?? ''}/tasks/${row.task_id}`}
                />
              )}
            </td>
            <td className="px-3 py-2">
              <span
                className={
                  row.resolved_at ? 'text-fg-muted' : 'text-feedback-warning'
                }
              >
                {row.resolved_at ? 'resolved' : 'open'}
              </span>
            </td>
            <td className="px-3 py-2 text-right font-mono text-xs">
              {delta(row.raised_at, row.resolved_at)}
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}
