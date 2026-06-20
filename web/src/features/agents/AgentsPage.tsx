/**
 * AgentsPage — two-pane Pasture layout.
 *
 * LEFT PANE: agent roster list at w-rail (244px) with Pasture card styling,
 * role-colored led dot, active-state left-marker accent bar.
 *
 * RIGHT PANE: AgentDetailPane — editable executor segmented control,
 * repo/tool chips as rounded-full tags, accountability metrics with
 * display font, real saves. Calm empty-state when no agent selected.
 *
 * NO autonomy toggle (founder ruling). NO permission-model changes.
 */
import { useState } from 'react';
import { useNavigate, useParams, useSearchParams } from 'react-router-dom';
import { PageHeader } from '@/design-system/patterns/PageHeader';
import {
  Tabs,
  TabsList,
  TabsTrigger,
  TabsContent,
} from '@/design-system/primitives/Tabs';
import { EmptyState } from '@/design-system/patterns/EmptyState';
import { Button } from '@/design-system/primitives/Button';
import { useAgentsList, useAgentsRoutes } from '@/hooks/agents';
import { useDensity } from '@/hooks/density';
import { PendingEnrollmentsTab } from './PendingEnrollmentsTab';
import { AgentDetailPane } from './AgentDetailPane';
import { AddAgentDialog } from './AddAgentDialog';

export function AgentsPage(): JSX.Element {
  const { agent_name: openAgentName } = useParams<{ agent_name?: string }>();
  const [searchParams] = useSearchParams();
  const navigate = useNavigate();
  const routes = useAgentsRoutes();
  const agentsQuery = useAgentsList();
  const { density } = useDensity();
  const rowPad = density === 'compact' ? 'py-1.5' : 'py-2.5';
  const [addOpen, setAddOpen] = useState(false);

  // A selected agent pins the Active tab — Pending under a detail view
  // doesn't make sense. The URL's `view=pending` is otherwise the source
  // of truth so refresh + back/forward both round-trip.
  const tab =
    openAgentName
      ? 'active'
      : searchParams.get('view') === 'pending'
        ? 'pending'
        : 'active';

  const onTabChange = (next: string) => {
    if (next === 'pending') navigate(routes.pending());
    else navigate(routes.inbox());
  };

  const agents = agentsQuery.data?.agents ?? [];
  const selectedAgent = typeof openAgentName === 'string' ? openAgentName : null;

  return (
    <div className="bg-surface-canvas flex h-full flex-col">
      {/* --- Top bar --- */}
      <header className="border-border-subtle border-b p-4">
        <div className="flex items-start justify-between gap-3">
          <PageHeader
            title="Agents"
            meta="Editable roster — click an agent to view and edit details."
          />
          <Button onClick={() => setAddOpen(true)}>Add agent</Button>
        </div>
        <Tabs value={tab} onValueChange={onTabChange} className="mt-3">
          <TabsList>
            <TabsTrigger value="active">Active</TabsTrigger>
            <TabsTrigger value="pending">Pending</TabsTrigger>
          </TabsList>
        </Tabs>
      </header>

      {/* --- Two-pane body --- */}
      <div className="flex flex-1 overflow-hidden">
        {/* LEFT: Roster list — Pasture w-rail (244px) */}
        <aside className="border-border-subtle bg-surface-sunken w-rail shrink-0 overflow-y-auto border-r">
          <Tabs value={tab}>
            <TabsContent value="active" className="mt-0">
              {agentsQuery.isLoading ? (
                <div className="animate-pulse space-y-2 p-3">
                  {[1, 2, 3, 4, 5].map((i) => (
                    <div
                      key={i}
                      className="bg-surface-raised h-10 w-full rounded-lg"
                    />
                  ))}
                </div>
              ) : agents.length === 0 ? (
                <div className="p-4">
                  <EmptyState
                    title="No agents enrolled"
                    body="Add a manager to create your first team."
                    cta={{
                      label: 'Add agent',
                      onClick: () => setAddOpen(true),
                    }}
                  />
                </div>
              ) : (
                <ul className="flex flex-col gap-1 p-2">
                  {agents.map((a) => {
                    const active = selectedAgent === a.name;
                    return (
                      <li key={a.name}>
                        <button
                          type="button"
                          onClick={() => navigate(routes.detail(a.name))}
                          className={`hover:bg-surface-hover relative w-full overflow-hidden px-3 ${rowPad} rounded-lg text-left transition-colors ${
                            active
                              ? 'bg-accent-muted border-accent-muted shadow-pasture-sm border'
                              : 'bg-surface hover:border-border-default border border-transparent'
                          }`}
                        >
                          {active && (
                            <span
                              aria-hidden="true"
                              className="bg-accent absolute top-1 bottom-1 left-0 w-0.5 rounded-full"
                            />
                          )}
                          <div className="flex items-center gap-2">
                            <span className="font-display text-text-primary truncate text-sm font-medium">
                              {a.name}
                            </span>
                            <span
                              aria-hidden="true"
                              className={`inline-block h-1.5 w-1.5 shrink-0 rounded-full ${
                                a.role === 'manager'
                                  ? 'bg-agent-manager'
                                  : 'bg-agent-worker'
                              }`}
                            />
                          </div>
                          <div className="text-text-muted mt-0.5 text-xs tabular-nums">
                            {a.team ?? 'No team'} · {a.executor ?? 'No executor'}
                          </div>
                          {a.description && (
                            <p className="text-text-muted mt-0.5 truncate text-xs leading-relaxed">
                              {a.description}
                            </p>
                          )}
                        </button>
                      </li>
                    );
                  })}
                </ul>
              )}
            </TabsContent>
            <TabsContent value="pending" className="mt-0 p-3">
              <PendingEnrollmentsTab />
            </TabsContent>
          </Tabs>
        </aside>

        {/* RIGHT: Detail/Edit pane */}
        <main className="bg-surface-canvas flex-1 overflow-hidden">
          {selectedAgent ? (
            <AgentDetailPane
              agentName={selectedAgent}
              onClose={() => navigate(routes.inbox())}
            />
          ) : (
            <div className="text-text-muted flex h-full items-center justify-center">
              <div className="text-center">
                <p className="font-display text-text-primary text-lg font-medium">
                  {agents.length > 0
                    ? 'Select an agent'
                    : 'No agents yet'}
                </p>
                <p className="text-text-muted mt-2 text-sm">
                  {agents.length > 0
                    ? 'Select an agent from the roster to view and edit details.'
                    : 'Add a manager to create your first team.'}
                </p>
              </div>
            </div>
          )}
        </main>
      </div>

      <AddAgentDialog open={addOpen} onOpenChange={setAddOpen} />
    </div>
  );
}
