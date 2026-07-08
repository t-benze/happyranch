/**
 * AgentsPage — two-pane Pasture layout.
 *
 * LEFT PANE: agent roster list at w-rail (244px) with Pasture card styling,
 * role-colored avatar-initial chip, a `role`-derived meta line, role-colored
 * led dot, active-state left-marker accent bar.
 *
 * RIGHT PANE: AgentDetailPane — editable executor segmented control,
 * repo/tool chips as rounded-full tags, accountability metrics with
 * display font, real saves. Calm empty-state when no agent selected.
 *
 * NO autonomy toggle (founder ruling). NO permission-model changes.
 */
import { useEffect, useMemo, useRef, useState } from 'react';
import { Plus } from 'lucide-react';
import { useNavigate, useParams, useSearchParams } from 'react-router-dom';
import { NewThreadDialog } from '@/shared/threads/NewThreadDialog';
import { useThreadRoutes } from '@/hooks/threads';
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
import type { AgentSummary } from '@/lib/api/types';
import { AddAgentDialog } from './AddAgentDialog';
import { AgentAvatar } from './AgentAvatar';

/**
 * AGENTS-02: capitalized role label for the roster meta line. The
 * Direction-A meta is `role · status`, but `status` is NOT a field on the
 * AgentSummary roster payload — so the meta is built role-only: the absent
 * half is omitted, never fabricated.
 */
function roleLabel(role: string | null): string {
  if (role === 'manager') return 'Manager';
  if (role === 'worker') return 'Worker';
  return 'No role';
}

export function AgentsPage(): JSX.Element {
  const { agent_name: openAgentName } = useParams<{ agent_name?: string }>();
  const [searchParams] = useSearchParams();
  const navigate = useNavigate();
  const routes = useAgentsRoutes();
  const agentsQuery = useAgentsList();
  const { density } = useDensity();
  const rowPad = density === 'compact' ? 'py-1.5' : 'py-2.5';
  const [addOpen, setAddOpen] = useState(false);

  // Start Thread dialog state
  const threadRoutes = useThreadRoutes();
  const [showStartThread, setShowStartThread] = useState(false);
  const [startThreadPrefill, setStartThreadPrefill] = useState<
    { recipients: string[] } | undefined
  >(undefined);

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

  // Memoized so the auto-select effect's dependency array is stable across
  // renders (the raw `?? []` fallback would be a fresh array each render).
  const agents = useMemo(
    () => agentsQuery.data?.agents ?? [],
    [agentsQuery.data?.agents],
  );
  const selectedAgent = typeof openAgentName === 'string' ? openAgentName : null;

  // AGENTS-01: on first settle of the active roster surface, auto-select the
  // first agent so its detail pane renders by default (matching the
  // Direction-A `a-agents` reference). A ref latches this to fire once — so
  // closing the detail pane (→ inbox) still returns to the calm empty state
  // instead of immediately re-selecting. Deep-links, the empty roster, and
  // the pending view are all left untouched.
  const didAutoSelect = useRef(false);
  useEffect(() => {
    if (didAutoSelect.current) return;
    if (agentsQuery.isLoading) return;
    if (searchParams.get('view') === 'pending') return;
    didAutoSelect.current = true;
    if (!selectedAgent && agents.length > 0) {
      navigate(routes.detail(agents[0].name), { replace: true });
    }
  }, [
    agentsQuery.isLoading,
    searchParams,
    selectedAgent,
    agents,
    navigate,
    routes,
  ]);

  const handleStartThread = (agent: AgentSummary) => {
    setStartThreadPrefill({ recipients: [agent.name] });
    setShowStartThread(true);
  };

  return (
    <div className="bg-surface-canvas flex h-full flex-col">
      {/* --- Top bar --- */}
      <header className="border-border-subtle border-b p-4">
        <div className="flex items-start justify-between gap-3">
          <PageHeader
            title="Agents"
            meta="Editable roster — click an agent to view and edit details."
          />
          {/* AGENTS-03: align the primary action to the Direction-A
              `a-agents` reference — leading "+" glyph + "New agent" label
              (was "Add agent"). The reference renders this in the app bar;
              relocating it into the shared AppShell AppBar is a cross-surface
              change held out of this presentation-only single-surface fix. */}
          <Button onClick={() => setAddOpen(true)}>
            <Plus aria-hidden="true" />
            New agent
          </Button>
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
                          <div className="flex items-start gap-2.5">
                            {/* AGENTS-02: role-colored avatar-initial chip,
                                mirroring the existing led-dot role logic. */}
                            <AgentAvatar name={a.name} role={a.role} size="sm" />
                            <div className="min-w-0 flex-1">
                              <div className="flex items-center gap-2">
                                <span className="font-display text-text-primary truncate text-sm font-medium">
                                  {a.name}
                                </span>
                                {/* AGENTS-04: role dot on the row's right edge
                                    (Direction-A `a-agents`), not crowding the
                                    name. Role-colored — never an active/idle
                                    status the roster payload doesn't carry. */}
                                <span
                                  aria-hidden="true"
                                  className={`ml-auto inline-block h-1.5 w-1.5 shrink-0 rounded-full ${
                                    a.role === 'manager'
                                      ? 'bg-agent-manager'
                                      : 'bg-agent-worker'
                                  }`}
                                />
                              </div>
                              <div className="text-text-muted mt-0.5 text-xs">
                                {roleLabel(a.role)}
                              </div>
                              {a.description && (
                                <p className="text-text-muted mt-0.5 truncate text-xs leading-relaxed">
                                  {a.description}
                                </p>
                              )}
                            </div>
                          </div>
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
              onStartThread={
                agents.find((a) => a.name === selectedAgent)
                  ? () => handleStartThread(agents.find((a) => a.name === selectedAgent)!)
                  : undefined
              }
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

      <NewThreadDialog
        open={showStartThread}
        onClose={() => setShowStartThread(false)}
        prefill={startThreadPrefill}
        onCreated={(newId) => navigate(threadRoutes.detail(newId))}
        agents={agents}
      />
    </div>
  );
}
