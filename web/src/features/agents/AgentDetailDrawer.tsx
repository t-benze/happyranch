/**
 * AgentDetailDrawer — opens when `:agent_name` is in the URL. Slides in
 * from the right (480px) over the active tab.
 *
 * Sections:
 *   1. Header — AgentChip, TierBadge, role + team metadata.
 *   2. Metadata — executor + description.
 *   3. Recent tasks — list of TaskCards filtered by `assigned_agent`.
 *   4. Learnings — read-only list (writes are agent-callback only).
 *
 * The learnings query may 412 ("workspace_not_migrated") on pre-migration
 * workspaces — we render an explanatory hint rather than a hard error so
 * the founder can still inspect tasks + scorecards for legacy agents.
 */
import { useNavigate, useParams } from 'react-router-dom';
import { Link } from 'react-router-dom';
import {
  Drawer,
  DrawerContent,
  DrawerDescription,
  DrawerTitle,
} from '@/design-system/primitives/Drawer';
import { AgentChip } from '@/design-system/patterns/AgentChip';
import { TierBadge } from '@/design-system/patterns/TierBadge';
import { TaskCard } from '@/design-system/patterns/TaskCard';
import { EmptyState } from '@/design-system/patterns/EmptyState';
import { ApiError } from '@/lib/api';
import {
  useAgentLearnings,
  useAgentsList,
  useAgentsRoutes,
  useAgentTasks,
} from '@/hooks/agents';
import { useTasksRoutes } from '@/hooks/tasks';
import { useJobsList } from '@/hooks/jobs';
import { useDensity } from '@/hooks/density';

interface AgentDetailDrawerProps {
  agentName: string;
}

export function AgentDetailDrawer({ agentName }: AgentDetailDrawerProps): JSX.Element {
  const navigate = useNavigate();
  const { slug } = useParams<{ slug: string }>();
  const agentsRoutes = useAgentsRoutes();
  const taskRoutes = useTasksRoutes();
  const { density } = useDensity();

  const agentsQuery = useAgentsList();
  const tasksQuery = useAgentTasks(agentName);
  const learningsQuery = useAgentLearnings(agentName);
  const jobsQuery = useJobsList({ agent: agentName, status: 'all', limit: 10 });

  const agent = agentsQuery.data?.agents.find((a) => a.name === agentName);
  const onClose = () => navigate(agentsRoutes.inbox());

  const learningsError =
    learningsQuery.isError && learningsQuery.error instanceof ApiError
      ? learningsQuery.error
      : null;

  return (
    <Drawer open onOpenChange={(o) => !o && onClose()}>
      <DrawerContent>
        <header className="border-border-subtle border-b p-4">
          <DrawerTitle className="text-fg flex items-center gap-3 text-lg">
            <AgentChip name={agentName} role={agent?.role ?? 'worker'} />
            {agent && <TierBadge tier={agent.tier} />}
          </DrawerTitle>
          <DrawerDescription className="text-fg-muted mt-2 text-xs">
            {agent ? (
              <>
                <span>team: {agent.team ?? '—'}</span>
                {agent.executor && <span> · executor: {agent.executor}</span>}
              </>
            ) : (
              'Loading…'
            )}
          </DrawerDescription>
          {agent?.description && (
            <p className="text-fg mt-2 text-sm">{agent.description}</p>
          )}
        </header>

        <section className="flex-1 overflow-y-auto p-4">
          <h3 className="text-fg-muted mb-2 text-xs font-medium tracking-wider uppercase">
            Recent tasks
          </h3>
          {tasksQuery.isLoading ? (
            <p className="text-fg-muted text-xs">Loading tasks…</p>
          ) : tasksQuery.data && tasksQuery.data.tasks.length > 0 ? (
            <ul className="space-y-2">
              {tasksQuery.data.tasks.map((t) => (
                <li key={t.task_id}>
                  <TaskCard
                    task={t}
                    to={taskRoutes.detail(t.task_id)}
                    density={density}
                  />
                </li>
              ))}
            </ul>
          ) : (
            <p className="text-fg-muted text-xs">
              No tasks where this agent was the assigned manager.
            </p>
          )}

          <h3 className="text-fg-muted mt-6 mb-2 text-xs font-medium tracking-wider uppercase">
            Learnings
          </h3>
          {learningsQuery.isLoading ? (
            <p className="text-fg-muted text-xs">Loading learnings…</p>
          ) : learningsError?.status === 412 ? (
            <p className="text-fg-muted text-xs">
              This workspace hasn't been migrated to the per-entry learnings
              layout yet. Run <code>grassland learning reindex</code> from the
              CLI to upgrade.
            </p>
          ) : learningsError ? (
            <p className="text-tier-red text-xs">
              Failed to load learnings ({learningsError.status}).
            </p>
          ) : learningsQuery.data && learningsQuery.data.entries.length > 0 ? (
            <ul className="space-y-2">
              {learningsQuery.data.entries.map((e) => (
                <li
                  key={e.id}
                  className="border-border-subtle bg-surface-raised rounded-md border p-2"
                >
                  <div className="flex items-center gap-2 text-xs">
                    <span className="text-fg-muted font-mono">{e.id}</span>
                    <span className="text-fg-muted">·</span>
                    <span className="text-fg-muted">{e.topic}</span>
                  </div>
                  <p className="text-fg mt-1 text-sm">{e.title}</p>
                </li>
              ))}
            </ul>
          ) : (
            <EmptyState
              title="No learnings"
              body="This agent has not filed any learnings yet."
            />
          )}

          {jobsQuery.data && jobsQuery.data.jobs.length > 0 && (
            <>
              <h3 className="text-fg-muted mt-6 mb-2 text-xs font-medium tracking-wider uppercase">
                Recent jobs
              </h3>
              <ul className="space-y-1 text-sm">
                {jobsQuery.data.jobs.map((j) => (
                  <li key={j.id}>
                    {slug ? (
                      <Link
                        to={`/orgs/${slug}/jobs/${j.id}`}
                        className="text-accent hover:underline font-mono"
                      >
                        {j.id}
                      </Link>
                    ) : (
                      <span className="font-mono">{j.id}</span>
                    )}
                    {' — '}
                    {j.title}{' '}
                    <span className="text-fg-muted">({j.status})</span>
                  </li>
                ))}
              </ul>
            </>
          )}
        </section>
      </DrawerContent>
    </Drawer>
  );
}
