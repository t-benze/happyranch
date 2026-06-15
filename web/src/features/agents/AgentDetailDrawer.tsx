/**
 * AgentDetailDrawer — opens when `:agent_name` is in the URL. Slides in
 * from the right (480px) over the active tab.
 *
 * Sections:
 *   1. Header — AgentChip, role + team metadata.
 *   2. Metadata — executor + description.
 *   3. Recent tasks — list of TaskCards filtered by `assigned_agent`.
 *   4. Learnings — read-only list (writes are agent-callback only).
 *
 * The learnings query may 412 ("workspace_not_migrated") on pre-migration
 * workspaces — we render an explanatory hint rather than a hard error so
 * the founder can still inspect tasks for legacy agents.
 */
import { useState } from 'react';
import { useNavigate, useParams } from 'react-router-dom';
import { Link } from 'react-router-dom';
import { ChevronDown, ChevronRight } from 'lucide-react';
import {
  Drawer,
  DrawerContent,
  DrawerDescription,
  DrawerTitle,
} from '@/design-system/primitives/Drawer';
import { AgentChip } from '@/design-system/patterns/AgentChip';
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
  const [showPrompt, setShowPrompt] = useState(false);

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
          {agent && agent.repos && Object.keys(agent.repos).length > 0 && (
            <div className="mt-2">
              <p className="text-fg-muted text-xs font-medium mb-1">Repositories</p>
              <div className="flex flex-wrap gap-1">
                {Object.entries(agent.repos).map(([key, _url]) => (
                  <span
                    key={key}
                    className="bg-bg-raised border-border text-fg-muted inline-flex items-center rounded border px-2 py-0.5 text-xs"
                  >
                    {key}
                  </span>
                ))}
              </div>
            </div>
          )}
        </header>

        {agent?.system_prompt && (
          <div className="border-border-subtle border-b px-4 py-3">
            <button
              type="button"
              onClick={() => setShowPrompt(!showPrompt)}
              className="text-fg-muted hover:text-fg flex w-full items-center gap-1 text-xs font-medium tracking-wider uppercase transition-colors"
            >
              {showPrompt ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
              System prompt
            </button>
            {showPrompt && (
              <pre className="bg-bg-raised border-border mt-2 max-h-48 overflow-auto rounded border p-3 text-xs whitespace-pre-wrap">
                {agent.system_prompt}
              </pre>
            )}
          </div>
        )}

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
              layout yet. Run <code>happyranch learning reindex</code> from the
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
