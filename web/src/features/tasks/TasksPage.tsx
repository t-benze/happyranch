import { useMemo, useState } from 'react';
import { useParams } from 'react-router-dom';
import { FilterSidebar, type FilterGroup } from '@/design-system/patterns/FilterSidebar';
import { TaskCard } from '@/design-system/patterns/TaskCard';
import { EmptyState } from '@/design-system/patterns/EmptyState';
import { useTasksList, useTasksRoutes } from '@/hooks/tasks';
import { useDensity } from '@/hooks/density';
import { TaskDetailPane } from './TaskDetailPane';

const STATUSES: FilterGroup['options'] = [
  { value: 'pending', label: 'Pending' },
  { value: 'in_progress', label: 'In progress' },
  { value: 'blocked', label: 'Blocked' },
  { value: 'completed', label: 'Completed' },
  { value: 'failed', label: 'Failed' },
];

export function TasksPage(): JSX.Element {
  const { task_id: openTaskId } = useParams<{ task_id: string }>();
  const [filters, setFilters] = useState<Record<string, string | null>>({
    status: null,
    team: null,
  });
  const { density } = useDensity();
  const routes = useTasksRoutes();
  const tasksQuery = useTasksList(
    filters.status ? { status: filters.status } : undefined,
  );

  const filtered = useMemo(() => {
    const all = tasksQuery.data?.tasks ?? [];
    return filters.team ? all.filter((t) => t.team === filters.team) : all;
  }, [tasksQuery.data, filters.team]);

  const teams = useMemo(() => {
    const set = new Set<string>();
    (tasksQuery.data?.tasks ?? []).forEach((t) => set.add(t.team));
    return [...set].sort();
  }, [tasksQuery.data]);

  const groups: FilterGroup[] = [
    { key: 'status', label: 'Status', options: STATUSES },
    { key: 'team', label: 'Team', options: teams.map((t) => ({ value: t, label: t })) },
  ];

  return (
    <div className="flex h-full">
      <FilterSidebar groups={groups} value={filters} onChange={setFilters} />
      <main className="bg-surface-canvas flex-1 overflow-y-auto p-4">
        {tasksQuery.isLoading ? (
          <p className="text-fg-muted">Loading…</p>
        ) : filtered.length === 0 ? (
          <EmptyState title="No tasks" body="No tasks match the current filters." />
        ) : (
          <ul className="space-y-2">
            {filtered.map((t) => (
              <li key={t.task_id}>
                <TaskCard
                  task={t}
                  to={routes.detail(t.task_id)}
                  active={openTaskId === t.task_id}
                  density={density}
                />
              </li>
            ))}
          </ul>
        )}
      </main>
      {openTaskId && <TaskDetailPane taskId={openTaskId} />}
    </div>
  );
}
