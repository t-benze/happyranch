/**
 * AgentsPage — single-canvas surface (UI_SPEC §11). Two sub-tabs:
 *
 *   - Active: scorecard table → calibration table.
 *   - Pending: enrollment list with approve/reject actions.
 *
 * Tab state rides on a `?view=pending` search param rather than a static
 * path segment — agent names are arbitrary `[a-z][a-z0-9_]*` so any
 * static `agents/<word>` sibling of `agents/:agent_name` would silently
 * shadow a real agent with that name. The agent detail Drawer mounts on
 * top of the Active tab when `:agent_name` is present (and forces the
 * Active tab — a Pending list under a per-agent drawer makes no sense).
 */
import {
  useNavigate,
  useParams,
  useSearchParams,
} from 'react-router-dom';
import { PageHeader } from '@/design-system/patterns/PageHeader';
import {
  Tabs,
  TabsList,
  TabsTrigger,
  TabsContent,
} from '@/design-system/primitives/Tabs';
import { EmptyState } from '@/design-system/patterns/EmptyState';
import { useAgentsList, useAgentsRoutes } from '@/hooks/agents';
import { AgentScorecardTable } from './AgentScorecardTable';
import { AgentCalibrationTable } from './AgentCalibrationTable';
import { PendingEnrollmentsTab } from './PendingEnrollmentsTab';
import { AgentDetailDrawer } from './AgentDetailDrawer';

export function AgentsPage(): JSX.Element {
  const { agent_name: openAgentName } = useParams<{ agent_name?: string }>();
  const [searchParams] = useSearchParams();
  const navigate = useNavigate();
  const routes = useAgentsRoutes();
  const agentsQuery = useAgentsList();

  // A per-agent drawer pins the Active tab — Pending under a detail view
  // doesn't make sense as a state. The URL's `view=pending` is otherwise
  // the source of truth so refresh + back/forward both round-trip.
  const tab =
    openAgentName ? 'active'
    : searchParams.get('view') === 'pending' ? 'pending'
    : 'active';

  const onTabChange = (next: string) => {
    if (next === 'pending') navigate(routes.pending());
    else navigate(routes.inbox());
  };

  const agents = agentsQuery.data?.agents ?? [];

  return (
    <div className="bg-surface-canvas flex h-full flex-col">
      <header className="border-border-subtle border-b p-4">
        <PageHeader
          title="Agents"
          meta="30-day rolling — tier, calibration, pending enrollments."
        />
        <Tabs value={tab} onValueChange={onTabChange} className="mt-3">
          <TabsList>
            <TabsTrigger value="active">Active</TabsTrigger>
            <TabsTrigger value="pending">Pending</TabsTrigger>
          </TabsList>
        </Tabs>
      </header>

      <main className="flex-1 overflow-y-auto p-4">
        <Tabs value={tab}>
          <TabsContent value="active">
            {agentsQuery.isLoading ? (
              <p className="text-fg-muted">Loading…</p>
            ) : agents.length === 0 ? (
              <EmptyState
                title="No agents yet"
                body="Run grassland agents init to bootstrap the team."
              />
            ) : (
              <div className="space-y-6">
                <section>
                  <h3 className="text-fg-muted mb-2 text-xs font-medium tracking-wider uppercase">
                    Scorecards
                  </h3>
                  <AgentScorecardTable agents={agents} activeName={openAgentName} />
                </section>
                <section>
                  <h3 className="text-fg-muted mb-2 text-xs font-medium tracking-wider uppercase">
                    Calibration
                  </h3>
                  <AgentCalibrationTable agents={agents} />
                </section>
              </div>
            )}
          </TabsContent>
          <TabsContent value="pending">
            <PendingEnrollmentsTab />
          </TabsContent>
        </Tabs>
      </main>

      {openAgentName && <AgentDetailDrawer agentName={openAgentName} />}
    </div>
  );
}
