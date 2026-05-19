import { useMemo } from 'react';
import { useParams, useSearchParams } from 'react-router-dom';
import { IdBadge } from '@/design-system/patterns/IdBadge';
import { EmptyState } from '@/design-system/patterns/EmptyState';
import { useAuditList } from '@/hooks/audit';
import { decodeFilters, sinceToISO } from './audit-filters';
import { foldEscalations } from './escalation-fold';

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
  // Pull a wider window than Activity so the FIFO fold can pair multi-cycle
  // escalate / resolved pairs without a second round-trip.
  const auditQuery = useAuditList({
    agent: filters.agent,
    since: sinceToISO(filters.since),
    limit: 500,
  });

  if (auditQuery.isLoading) return <p className="text-fg-muted">Loading…</p>;
  const entries = auditQuery.data?.entries ?? [];
  const folded = foldEscalations(entries);
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
            key={`${row.task_id ?? 'no-task'}-${row.raised_at}-${i}`}
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
