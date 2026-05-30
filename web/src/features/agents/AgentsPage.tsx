/**
 * AgentsPage — single-canvas surface. Two sub-tabs:
 *
 *   - Active: name + team + role + executor + description.
 *   - Pending: enrollment list with approve/reject actions.
 *
 * Tab state rides on a `?view=pending` search param rather than a static
 * path segment — agent names are arbitrary `[a-z][a-z0-9_]*` so any
 * static `agents/<word>` sibling of `agents/:agent_name` would silently
 * shadow a real agent with that name. The agent detail Drawer mounts on
 * top of the Active tab when `:agent_name` is present (and forces the
 * Active tab — a Pending list under a per-agent drawer makes no sense).
 */
import { useState } from 'react';
import { Link } from 'react-router-dom';
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
import { AgentChip } from '@/design-system/patterns/AgentChip';
import { Button } from '@/design-system/primitives/Button';
import { useAgentsList, useAgentsRoutes } from '@/hooks/agents';
import { useDensity } from '@/hooks/density';
import { PendingEnrollmentsTab } from './PendingEnrollmentsTab';
import { AgentDetailDrawer } from './AgentDetailDrawer';
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
        <div className="flex items-start justify-between gap-3">
          <PageHeader
            title="Agents"
            meta="Active roster + pending enrollments."
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

      <main className="flex-1 overflow-y-auto p-4">
        <Tabs value={tab}>
          <TabsContent value="active">
            {agentsQuery.isLoading ? (
              <p className="text-fg-muted">Loading…</p>
            ) : agents.length === 0 ? (
              <div>
                <EmptyState
                  title="No agents yet"
                  body="Add a manager to create your first team."
                />
                <div className="mt-4 flex justify-center">
                  <Button onClick={() => setAddOpen(true)}>Add agent</Button>
                </div>
              </div>
            ) : (
              <div className="border-border-subtle overflow-hidden rounded-lg border">
                <table className="w-full text-sm">
                  <thead className="bg-surface-sunken text-fg-muted text-xs tracking-wider uppercase">
                    <tr>
                      <th className="px-3 py-2 text-left font-medium">Agent</th>
                      <th className="px-3 py-2 text-left font-medium">Team</th>
                      <th className="px-3 py-2 text-left font-medium">Executor</th>
                      <th className="px-3 py-2 text-left font-medium">Description</th>
                    </tr>
                  </thead>
                  <tbody>
                    {agents.map((a) => {
                      const active = openAgentName === a.name;
                      return (
                        <tr
                          key={a.name}
                          className={`border-border-subtle border-t ${
                            active ? 'bg-accent-muted' : 'hover:bg-surface-raised/60'
                          }`}
                        >
                          <td className={`px-3 ${rowPad}`}>
                            <Link to={routes.detail(a.name)} className="hover:underline">
                              <AgentChip name={a.name} role={a.role ?? 'worker'} />
                            </Link>
                          </td>
                          <td className={`text-fg-muted px-3 ${rowPad}`}>
                            {a.team ?? '—'}
                          </td>
                          <td className={`text-fg-muted px-3 ${rowPad}`}>
                            {a.executor ?? '—'}
                          </td>
                          <td className={`text-fg-muted px-3 ${rowPad}`}>
                            {a.description ?? '—'}
                          </td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
            )}
          </TabsContent>
          <TabsContent value="pending">
            <PendingEnrollmentsTab />
          </TabsContent>
        </Tabs>
      </main>

      <AddAgentDialog open={addOpen} onOpenChange={setAddOpen} />
      {openAgentName && <AgentDetailDrawer agentName={openAgentName} />}
    </div>
  );
}
