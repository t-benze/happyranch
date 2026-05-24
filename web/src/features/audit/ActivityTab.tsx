import { useMemo } from 'react';
import { useParams, useSearchParams } from 'react-router-dom';
import { AuditRow } from '@/design-system/patterns/AuditRow';
import { EmptyState } from '@/design-system/patterns/EmptyState';
import { useAuditList } from '@/hooks/audit';
import { useDensity } from '@/hooks/density';
import { decodeFilters, sinceToISO } from './audit-filters';

export function ActivityTab(): JSX.Element {
  const { slug } = useParams<{ slug: string }>();
  const [searchParams] = useSearchParams();
  const filters = useMemo(() => decodeFilters(searchParams), [searchParams]);
  const { density } = useDensity();
  const auditQuery = useAuditList({
    agent: filters.agent,
    action: filters.action,
    since: sinceToISO(filters.since),
    task_id: filters.task_id,
    limit: 200,
  });

  if (auditQuery.isLoading) {
    return <p className="text-fg-muted">Loading…</p>;
  }
  const entries = auditQuery.data?.entries ?? [];
  if (entries.length === 0) {
    return (
      <EmptyState
        title="No audit entries"
        body="No audit entries match the current filters."
      />
    );
  }
  const sorted = [...entries].sort((a, b) =>
    a.created_at < b.created_at ? 1 : -1,
  );

  return (
    <ul aria-label="Audit entries">
      {sorted.map((e) => (
        <AuditRow
          key={e.id}
          entry={e}
          density={density}
          taskHref={
            e.task_id ? `/orgs/${slug ?? ''}/tasks/${e.task_id}` : undefined
          }
          agentHref={
            e.agent
              ? `/orgs/${slug ?? ''}/audit?agent=${encodeURIComponent(e.agent)}`
              : undefined
          }
          scriptsBasePath={slug ? `/orgs/${slug}/scripts` : undefined}
        />
      ))}
    </ul>
  );
}
