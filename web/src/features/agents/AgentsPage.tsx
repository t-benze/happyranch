/**
 * AgentsPage — two-pane layout (§4.4, design-overhaul).
 *
 * LEFT PANE: agent roster list with status dot + role strings from real
 * stored data. Click selects an agent → detail/edit appears in the right
 * pane. Pending enrollments tab available via search param `?view=pending`.
 *
 * RIGHT PANE: AgentDetailPane — editable executor, repo chips (real saves),
 * read-only system prompt + description (gap: no founder-facing update route),
 * accountability metrics (DERIVE from real tasks), recent tasks/learnings/jobs
 * with object-ID click-through.
 *
 * States: Loading skeleton, Empty roster (calm), Error with retry.
 * NO autonomy toggle (A1 deferred). NO dollar/cost meter.
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
import { AgentChip } from '@/design-system/patterns/AgentChip';
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
        {/* LEFT: Roster list */}
        <aside className="border-border-subtle bg-surface-sunken w-80 shrink-0 overflow-y-auto border-r">
          <Tabs value={tab}>
            <TabsContent value="active" className="mt-0">
              {agentsQuery.isLoading ? (
                <div className="animate-pulse space-y-2 p-3">
                  {[1, 2, 3, 4, 5].map((i) => (
                    <div
                      key={i}
                      className="bg-bg-raised h-10 w-full rounded"
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
                <ul className="divide-border-subtle divide-y">
                  {agents.map((a) => {
                    const active = selectedAgent === a.name;
                    return (
                      <li key={a.name}>
                        <button
                          type="button"
                          onClick={() => navigate(routes.detail(a.name))}
                          className={`hover:bg-surface-raised/60 w-full px-3 ${rowPad} text-left transition-colors ${
                            active ? 'bg-accent-muted' : ''
                          }`}
                        >
                          <div className="flex items-center gap-2">
                            <AgentChip name={a.name} role={a.role ?? 'worker'} />
                          </div>
                          <div className="text-fg-muted mt-0.5 text-xs">
                            {a.team ?? 'No team'} · {a.executor ?? 'No executor'}
                          </div>
                          {a.description && (
                            <p className="text-fg-muted mt-0.5 truncate text-xs">
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
            <div className="text-fg-muted flex h-full items-center justify-center">
              <div className="text-center">
                <p className="text-sm">Select an agent from the roster to view details.</p>
                <p className="mt-1 text-xs">Or add a new agent to get started.</p>
              </div>
            </div>
          )}
        </main>
      </div>

      <AddAgentDialog open={addOpen} onOpenChange={setAddOpen} />
    </div>
  );
}
