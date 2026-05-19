import { useMemo } from 'react';
import { Link, useParams, useSearchParams } from 'react-router-dom';
import { IdBadge } from '@/design-system/patterns/IdBadge';
import { TraceTree, type CostCell } from '@/design-system/patterns/TraceTree';
import { EmptyState } from '@/design-system/patterns/EmptyState';
import { useAuditList } from '@/hooks/audit';
import { useTaskRecall, useTasksRoutes } from '@/hooks/tasks';
import { useDensity } from '@/hooks/density';
import type { AuditEntry } from '@/lib/api/types';
import { decodeFilters, encodeFilters, sinceToISO } from './audit-filters';

function tokensFromPayload(p: Record<string, unknown>): number {
  const tu = p['token_usage'];
  if (tu && typeof tu === 'object' && 'total' in tu) {
    const t = (tu as Record<string, unknown>).total;
    if (typeof t === 'number') return t;
  }
  const tc = p['token_count'];
  return typeof tc === 'number' ? tc : 0;
}

function usdFromPayload(p: Record<string, unknown>): number | undefined {
  const tu = p['token_usage'];
  if (tu && typeof tu === 'object' && 'total_cost_usd' in tu) {
    const v = (tu as Record<string, unknown>).total_cost_usd;
    if (typeof v === 'number') return v;
  }
  return undefined;
}

function projectCosts(entries: AuditEntry[]): Record<string, CostCell> {
  const out: Record<string, CostCell> = {};
  for (const e of entries) {
    if (e.action !== 'session_end' || !e.task_id) continue;
    const cell = out[e.task_id] ?? { tokens: 0 };
    cell.tokens += tokensFromPayload(e.payload);
    const u = usdFromPayload(e.payload);
    if (u != null) cell.usd = (cell.usd ?? 0) + u;
    out[e.task_id] = cell;
  }
  return out;
}

interface TaskPickerRow {
  task_id: string;
  agent: string | null;
  latest: string;
}

function recentTaskIds(entries: AuditEntry[]): TaskPickerRow[] {
  const map = new Map<string, { agent: string | null; latest: string }>();
  for (const e of entries) {
    if (!e.task_id) continue;
    const existing = map.get(e.task_id);
    if (!existing || existing.latest < e.created_at) {
      map.set(e.task_id, { agent: e.agent, latest: e.created_at });
    }
  }
  return [...map.entries()]
    .map(([task_id, v]) => ({ task_id, ...v }))
    .sort((a, b) => (a.latest < b.latest ? 1 : -1));
}

export function TracesTab(): JSX.Element {
  const { slug, task_id: openTaskId } = useParams<{
    slug: string;
    task_id: string;
  }>();
  const [searchParams] = useSearchParams();
  const filters = useMemo(() => decodeFilters(searchParams), [searchParams]);
  const { density } = useDensity();
  const routes = useTasksRoutes();

  const auditQuery = useAuditList({
    agent: filters.agent,
    since: sinceToISO(filters.since),
    limit: 500,
  });

  const entries = useMemo(
    () => auditQuery.data?.entries ?? [],
    [auditQuery.data],
  );
  const tasks = useMemo(() => recentTaskIds(entries), [entries]);
  const costs = useMemo(() => projectCosts(entries), [entries]);

  const recallQuery = useTaskRecall(openTaskId);

  const traceBase = `/orgs/${slug ?? ''}/audit/traces`;
  const search = encodeFilters(filters);
  const suffix = search ? `?${search}` : '';

  return (
    <div className="flex h-full gap-4">
      <aside className="border-border-subtle w-72 shrink-0 overflow-y-auto border-r">
        <h3 className="text-fg-muted px-3 pt-3 text-xs font-medium tracking-wider uppercase">
          Recent tasks
        </h3>
        {tasks.length === 0 ? (
          <p className="text-fg-muted px-3 py-2 text-sm">No tasks in range.</p>
        ) : (
          <ul>
            {tasks.map((t) => (
              <li key={t.task_id}>
                <Link
                  to={`${traceBase}/${t.task_id}${suffix}`}
                  className={`hover:bg-surface-raised flex items-center gap-2 px-3 py-1.5 text-sm ${
                    openTaskId === t.task_id ? 'bg-accent-muted' : ''
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
          <p className="text-fg-muted">Loading recall…</p>
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
