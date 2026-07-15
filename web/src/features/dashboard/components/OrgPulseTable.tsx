/**
 * OrgPulseTable — per-team weekly acceptance + trend + sparkline.
 *
 * Consumes the new design-system Sparkline pattern. Feature-local for now
 * because no other surface needs the per-team table; the sparkline itself
 * is the reusable primitive.
 */
import { Sparkline } from '@/design-system/patterns/Sparkline';
import type { TeamPulseRow } from '@/lib/api/types';

interface OrgPulseTableProps {
  rows: TeamPulseRow[];
}

type Tier = 'green' | 'yellow' | 'red';

function tierFor(acceptance: number): Tier {
  if (acceptance >= 90) return 'green';
  if (acceptance >= 80) return 'yellow';
  return 'red';
}

const ACCEPTANCE_TEXT: Record<Tier, string> = {
  green: 'text-tier-green',
  yellow: 'text-tier-yellow',
  red: 'text-tier-red',
};

export function OrgPulseTable({ rows }: OrgPulseTableProps): JSX.Element {
  if (rows.length === 0) {
    return <p className="text-text-muted text-sm">No teams configured.</p>;
  }
  return (
    <table className="w-full text-sm">
      <tbody>
        {rows.map((r) => {
          const tier = tierFor(r.acceptance_pct);
          const trendSign =
            r.trend_delta > 0 ? '+' : r.trend_delta < 0 ? '−' : '';
          const trendValue = Math.abs(r.trend_delta);
          return (
            <tr
              key={r.team}
              className="border-border-subtle border-b last:border-b-0"
            >
              <td className="text-text-primary py-2 font-medium">{r.team}</td>
              <td className="text-text-muted py-2 font-mono text-xs">
                {r.members} agents
              </td>
              <td className="py-2">
                <Sparkline data={r.sparkline} variant={tier} />
              </td>
              <td
                className={`${ACCEPTANCE_TEXT[tier]} py-2 text-right font-mono text-xs`}
              >
                {r.acceptance_pct}%
              </td>
              <td className="text-text-muted py-2 pl-3 text-right font-mono text-xs">
                {trendSign}
                {trendValue || '—'}
              </td>
            </tr>
          );
        })}
      </tbody>
    </table>
  );
}
