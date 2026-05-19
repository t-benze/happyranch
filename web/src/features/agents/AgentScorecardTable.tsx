/**
 * AgentScorecardTable — the 30-day rolling roster pinned at the top of
 * the Agents page (UI_SPEC §11). One row per agent, sortable by acceptance.
 *
 * - Tier column uses the `TierBadge` pattern (the only surface where the
 *   tier color earns its rent on a whole badge).
 * - Rows are <Link>s into the AgentDetailDrawer (`/orgs/:slug/agents/:agent_name`).
 * - Honors `useDensity()` like the Tasks inbox does.
 */
import { Link } from 'react-router-dom';
import type { AgentSummary } from '@/lib/api/types';
import { AgentChip } from '@/design-system/patterns/AgentChip';
import { TierBadge } from '@/design-system/patterns/TierBadge';
import { useAgentsRoutes } from '@/hooks/agents';
import { useDensity } from '@/hooks/density';

function fmtPct(rate: number): string {
  return `${Math.round(rate * 100)}%`;
}

interface AgentScorecardTableProps {
  agents: AgentSummary[];
  activeName?: string;
}

export function AgentScorecardTable({
  agents,
  activeName,
}: AgentScorecardTableProps): JSX.Element {
  const routes = useAgentsRoutes();
  const { density } = useDensity();
  const rowPad = density === 'compact' ? 'py-1.5' : 'py-2.5';

  return (
    <div className="border-border-subtle overflow-hidden rounded-lg border">
      <table className="w-full text-sm">
        <thead className="bg-surface-sunken text-fg-muted text-xs tracking-wider uppercase">
          <tr>
            <th className="px-3 py-2 text-left font-medium">Agent</th>
            <th className="px-3 py-2 text-left font-medium">Team</th>
            <th className="px-3 py-2 text-left font-medium">Tier</th>
            <th className="px-3 py-2 text-right font-medium">Acceptance</th>
            <th className="px-3 py-2 text-right font-medium">Revision</th>
            <th className="px-3 py-2 text-right font-medium">Errors</th>
          </tr>
        </thead>
        <tbody>
          {agents.map((a) => {
            const sc = a.scorecard;
            const active = activeName === a.name;
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
                <td className={`px-3 ${rowPad}`}>
                  <TierBadge tier={a.tier} />
                </td>
                <td className={`px-3 text-right font-mono text-sm ${rowPad}`}>
                  {sc ? fmtPct(sc.acceptance_rate) : '—'}
                </td>
                <td className={`px-3 text-right font-mono text-sm ${rowPad}`}>
                  {sc ? fmtPct(sc.revision_rate) : '—'}
                </td>
                <td className={`px-3 text-right font-mono text-sm ${rowPad}`}>
                  {sc ? sc.error_count : '—'}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
