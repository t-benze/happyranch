/**
 * AgentCalibrationTable — per-agent gap between confidence and outcome
 * (protocol/05e-dashboard.md Page 2). One row per agent over the same
 * 30-day window as the scorecards. A positive gap means the agent is
 * over-confident; a negative gap means they are under-confident.
 *
 * Calibration data lights up when:
 *   - `avg_confidence` is non-null (at least one completion report with
 *     a confidence score in the window), AND
 *   - the agent has a scorecard whose `acceptance_rate` is the proxy for
 *     "actual accuracy."
 *
 * Otherwise the row renders dashes — never zeros, which would imply a
 * worst-case calibration that we have not actually measured.
 */
import { Link } from 'react-router-dom';
import type { AgentSummary } from '@/lib/api/types';
import { AgentChip } from '@/design-system/patterns/AgentChip';
import { useAgentsRoutes } from '@/hooks/agents';
import { useDensity } from '@/hooks/density';

interface AgentCalibrationTableProps {
  agents: AgentSummary[];
}

interface CalibrationRow {
  agent: AgentSummary;
  confidence: number | null;
  accuracy: number | null;
  gap: number | null;
}

function buildRow(a: AgentSummary): CalibrationRow {
  const confidence = a.avg_confidence;
  const accuracy = a.scorecard ? Math.round(a.scorecard.acceptance_rate * 100) : null;
  const gap =
    confidence != null && accuracy != null
      ? Math.round(confidence - accuracy)
      : null;
  return { agent: a, confidence, accuracy, gap };
}

function gapClass(gap: number | null): string {
  if (gap === null) return 'text-fg-muted';
  if (gap > 5) return 'text-tier-yellow';
  if (gap < -5) return 'text-tier-yellow';
  return 'text-fg';
}

export function AgentCalibrationTable({
  agents,
}: AgentCalibrationTableProps): JSX.Element {
  const routes = useAgentsRoutes();
  const { density } = useDensity();
  const rowPad = density === 'compact' ? 'py-1.5' : 'py-2.5';
  const rows = agents.map(buildRow);

  return (
    <div className="border-border-subtle overflow-hidden rounded-lg border">
      <table className="w-full text-sm">
        <thead className="bg-surface-sunken text-fg-muted text-xs tracking-wider uppercase">
          <tr>
            <th className="px-3 py-2 text-left font-medium">Agent</th>
            <th className="px-3 py-2 text-right font-medium">Avg confidence</th>
            <th className="px-3 py-2 text-right font-medium">Actual accuracy</th>
            <th className="px-3 py-2 text-right font-medium">Gap</th>
          </tr>
        </thead>
        <tbody>
          {rows.map(({ agent, confidence, accuracy, gap }) => (
            <tr
              key={agent.name}
              className="border-border-subtle hover:bg-surface-raised/60 border-t"
            >
              <td className={`px-3 ${rowPad}`}>
                <Link to={routes.detail(agent.name)} className="hover:underline">
                  <AgentChip name={agent.name} role={agent.role ?? 'worker'} />
                </Link>
              </td>
              <td className={`px-3 text-right font-mono text-sm ${rowPad}`}>
                {confidence != null ? `${confidence}%` : '—'}
              </td>
              <td className={`px-3 text-right font-mono text-sm ${rowPad}`}>
                {accuracy != null ? `${accuracy}%` : '—'}
              </td>
              <td
                className={`px-3 text-right font-mono text-sm ${rowPad} ${gapClass(gap)}`}
              >
                {gap != null ? (gap > 0 ? `+${gap}%` : `${gap}%`) : '—'}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
