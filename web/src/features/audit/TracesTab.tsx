import { useMemo } from 'react';
import { Link, useParams, useSearchParams } from 'react-router-dom';
import { IdBadge } from '@/design-system/patterns/IdBadge';
import { TraceTree } from '@/design-system/patterns/TraceTree';
import { EmptyState } from '@/design-system/patterns/EmptyState';
import { useAuditList } from '@/hooks/audit';
import { useTaskRecall, useTasksRoutes } from '@/hooks/tasks';
import { useDensity } from '@/hooks/density';
import { decodeFilters, encodeFilters, sinceToISO } from './audit-filters';
import { projectCosts, recentTaskIds } from './trace-projection';

export function TracesTab(): JSX.Element {
  const { slug, task_id: pathTaskId } = useParams<{
    slug: string;
    task_id: string;
  }>();
  const [searchParams] = useSearchParams();
  const filters = useMemo(() => decodeFilters(searchParams), [searchParams]);
  const { density } = useDensity();
  const routes = useTasksRoutes();

  // Two queries on purpose:
  //   1. picker — narrowed by the active agent filter (Threads deep-links
  //      arrive with `?agent=<lead participant>`; the picker should honor it)
  //   2. costs  — NOT narrowed by agent, because a task's recall tree may
  //      delegate to other agents whose session_end rows would otherwise be
  //      excluded and corrupt the per-task token/USD totals.
  const pickerQuery = useAuditList({
    agent: filters.agent,
    since: sinceToISO(filters.since),
    limit: 500,
  });
  const costQuery = useAuditList({
    action: 'session_end',
    since: sinceToISO(filters.since),
    limit: 500,
  });

  const pickerEntries = useMemo(
    () => pickerQuery.data?.pages.flatMap((p) => p.entries) ?? [],
    [pickerQuery.data],
  );
  const costEntries = useMemo(
    () => costQuery.data?.pages.flatMap((p) => p.entries) ?? [],
    [costQuery.data],
  );
  const tasks = useMemo(() => recentTaskIds(pickerEntries), [pickerEntries]);
  const costs = useMemo(() => projectCosts(costEntries), [costEntries]);

  // Honor both URL shapes:
  //   /audit/traces/:task_id          (canonical, set by the picker click)
  //   /audit/traces?task_id=TASK-N    (handoff from Activity's "View audit"
  //                                    deep link when the founder switches
  //                                    to the Traces sub-tab via SubTabBar)
  const openTaskId = pathTaskId ?? filters.task_id ?? undefined;

  const recallQuery = useTaskRecall(openTaskId);

  const traceBase = `/orgs/${slug ?? ''}/audit/traces`;
  // Strip task_id from the picker links' query suffix — the canonical URL
  // carries it as a path segment.
  const pickerSuffix = useMemo(() => {
    const s = encodeFilters({ ...filters, task_id: null });
    return s ? `?${s}` : '';
  }, [filters]);

  return (
    <div className="flex h-full gap-4">
      <aside className="border-border-default w-72 shrink-0 overflow-y-auto border-r">
        <h3 className="text-text-secondary font-display px-3 pt-3 text-sm font-medium">
          Recent tasks
        </h3>
        {tasks.length === 0 ? (
          <p className="text-text-muted px-3 py-2 text-sm">No tasks in range.</p>
        ) : (
          <ul>
            {tasks.map((t) => (
              <li key={t.task_id}>
                <Link
                  to={`${traceBase}/${t.task_id}${pickerSuffix}`}
                  className={`hover:bg-surface-hover flex items-center gap-2 px-3 py-1.5 text-sm ${
                    openTaskId === t.task_id ? 'bg-accent-soft text-accent-text' : ''
                  }`}
                >
                  <IdBadge kind="task" id={t.task_id} />
                  {t.agent && <span className="text-fg-muted">{t.agent}</span>}
                </Link>
              </li>
            ))}
          </ul>
        )}
      </aside>
      <section className="flex-1 overflow-y-auto">
        {!openTaskId ? (
          <EmptyState
            title="Pick a task"
            body="Select a task on the left to view its execution trace."
          />
        ) : recallQuery.isLoading ? (
          <p className="text-text-muted p-4">Loading recall…</p>
        ) : recallQuery.data ? (
          <TraceTree
            root={recallQuery.data}
            costs={costs}
            density={density}
            taskHref={(id) => routes.detail(id)}
          />
        ) : (
          <EmptyState
            title="No recall data"
            body="Recall tree unavailable for this task."
          />
        )}
      </section>
    </div>
  );
}
