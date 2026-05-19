/**
 * Founder dashboard / Live Status page (PR 12).
 *
 * Spec: `docs/superpowers/specs/2026-05-19-web-dashboard-design.md`.
 *
 * One `useTasksList({ limit: 200 })` query powers three cards (sliced
 * client-side by status + block_kind); one `useHealth()` query powers the
 * fourth. Read-only — clicking a task opens the Tasks feature.
 */
import { useMemo, type ReactNode } from 'react';
import { DashboardLayout } from '@/design-system/layouts/DashboardLayout';
import { EmptyState } from '@/design-system/patterns/EmptyState';
import { TaskCard } from '@/design-system/patterns/TaskCard';
import type { TaskRecord } from '@/lib/api/types';
import { useHealth } from '@/hooks/health';
import { useTasksList, useTasksRoutes } from '@/hooks/tasks';

const FETCH_LIMIT = 200;

function truncatePath(p: string | null, max = 48): string {
  if (!p) return '—';
  if (p.length <= max) return p;
  const parts = p.split('/').filter(Boolean);
  if (parts.length <= 2) return p;
  return `…/${parts.slice(-2).join('/')}`;
}

function byUpdatedDesc(a: TaskRecord, b: TaskRecord): number {
  return a.updated_at < b.updated_at ? 1 : a.updated_at > b.updated_at ? -1 : 0;
}

interface CardListProps {
  tasks: TaskRecord[];
  detailFor: (taskId: string) => string;
}

function CardTaskList({ tasks, detailFor }: CardListProps): JSX.Element {
  return (
    <ul className="space-y-2">
      {tasks.map((t) => (
        <li key={t.task_id}>
          <TaskCard task={t} to={detailFor(t.task_id)} density="compact" />
        </li>
      ))}
    </ul>
  );
}

function HealthBody(): JSX.Element {
  const q = useHealth();
  if (q.isLoading) {
    return <p className="text-text-muted text-sm">loading…</p>;
  }
  if (q.isError || !q.data) {
    return (
      <p className="text-sm">
        <span className="text-feedback-danger" aria-label="daemon unreachable">●</span>{' '}
        <span className="text-text-muted">daemon: unreachable</span>
      </p>
    );
  }
  const ok = q.data.status === 'ok';
  return (
    <div className="text-sm">
      <p>
        <span
          className={ok ? 'text-feedback-success' : 'text-feedback-danger'}
          aria-label={ok ? 'daemon ok' : 'daemon not ok'}
        >
          ●
        </span>{' '}
        <span className="text-text">daemon: {q.data.status}</span>
      </p>
      <p className="text-text-muted mt-1 font-mono text-xs" title={q.data.active_runtime ?? ''}>
        active runtime: {truncatePath(q.data.active_runtime)}
      </p>
    </div>
  );
}

export function DashboardPage(): JSX.Element {
  const tasksQuery = useTasksList({ limit: FETCH_LIMIT });
  const routes = useTasksRoutes();

  // Branch on loading/error BEFORE falling back to `?? []`. Otherwise a slow
  // or failed `/tasks` fetch renders reassuring empty states ("All clear",
  // "No active tasks") and hides real escalations from the founder.
  const tasksLoading = tasksQuery.isLoading;
  const tasksError = tasksQuery.isError;
  const all = tasksQuery.data?.tasks ?? [];

  function gate(body: ReactNode): ReactNode {
    if (tasksLoading) {
      return <p className="text-text-muted text-sm">loading…</p>;
    }
    if (tasksError) {
      return (
        <p className="text-feedback-danger text-sm">Failed to load tasks.</p>
      );
    }
    return body;
  }

  const escalated = useMemo(
    () =>
      all
        .filter((t) => t.status === 'blocked' && t.block_kind === 'escalated')
        .sort(byUpdatedDesc),
    [all],
  );

  const blockedDelegated = useMemo(
    () =>
      all
        .filter((t) => t.status === 'blocked' && t.block_kind !== 'escalated')
        .sort(byUpdatedDesc),
    [all],
  );

  const activeByTeam = useMemo(() => {
    const groups = new Map<string, TaskRecord[]>();
    for (const t of all) {
      if (t.status !== 'in_progress') continue;
      const list = groups.get(t.team) ?? [];
      list.push(t);
      groups.set(t.team, list);
    }
    return [...groups.entries()]
      .map(([team, list]) => [team, [...list].sort(byUpdatedDesc)] as const)
      .sort(([a], [b]) => a.localeCompare(b));
  }, [all]);

  const pending = escalated.length === 0 ? (
    <EmptyState
      title="All clear"
      body="No escalations waiting on the founder."
    />
  ) : (
    <CardTaskList tasks={escalated} detailFor={routes.detail} />
  );

  const blocked = blockedDelegated.length === 0 ? (
    <EmptyState
      title="No blocked tasks"
      body="Escalations awaiting your action appear in the panel above."
    />
  ) : (
    <CardTaskList tasks={blockedDelegated} detailFor={routes.detail} />
  );

  const active = activeByTeam.length === 0 ? (
    <EmptyState title="No active tasks" body="No tasks are running right now." />
  ) : (
    <div className="space-y-4">
      {activeByTeam.map(([team, tasks]) => (
        <section key={team}>
          <h3 className="text-text-secondary mb-2 text-xs font-semibold">
            {team}
            <span className="text-text-muted ml-2 font-normal">({tasks.length})</span>
          </h3>
          <CardTaskList tasks={tasks} detailFor={routes.detail} />
        </section>
      ))}
    </div>
  );

  return (
    <DashboardLayout
      health={<HealthBody />}
      pending={gate(pending)}
      activeByTeam={gate(active)}
      blocked={gate(blocked)}
    />
  );
}
