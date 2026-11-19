/**
 * OrgPulseTable — per-team weekly acceptance + trend + sparkline.
 *
 * Consumes the new design-system Sparkline pattern. Feature-local for now
 * because no other surface needs the per-team table; the sparkline itself
 * is the reusable primitive.
 */
import { Sparkline } from '@/design-system/patterns/Sparkline';
import type { TeamPulseRow } from '@/lib/api/types';
import { useTranslation } from '@/hooks/i18n';

interface OrgPulseTableProps {
  rows: TeamPulseRow[];
}

type Tier = 'green' | 'yellow';

/**
 * THR-376 r2 D7: colour by health, as the a-dashboard design does — the
 * accent (green tier) while a team holds or improves its week-on-week
 * acceptance, attention (yellow tier) when it is trending down. The old
 * absolute thresholds (<80% = red) painted almost every team red.
 */
function tierFor(trendDelta: number): Tier {
  return trendDelta < 0 ? 'yellow' : 'green';
}

const ACCEPTANCE_TEXT: Record<Tier, string> = {
  green: 'text-tier-green',
  yellow: 'text-tier-yellow',
};

/** THR-376 r2 G12: the design's 56x16 pulse sparkline. */
const SPARK_W = 56;

export function OrgPulseTable({ rows }: OrgPulseTableProps): JSX.Element {
  const { t } = useTranslation();
  if (rows.length === 0) {
    return <p className="text-text-muted text-sm">{t('dashboard.pulse.empty')}</p>;
  }
  return (
    <table className="w-full text-sm">
      <tbody>
        {rows.map((r) => {
          const tier = tierFor(r.trend_delta);
          // THR-376 r2 D8: a team with no agents has no pulse to show, so its
          // count, line, acceptance and trend read "—" instead of
          // "0 agents" / a flat line / 0%.
          const empty = r.members === 0;
          const membersLabel = empty ? '—' : t('dashboard.pulse.members', { count: r.members });
          const trendSign =
            r.trend_delta > 0 ? '+' : r.trend_delta < 0 ? '−' : '';
          const trendValue = Math.abs(r.trend_delta);
          return (
            <tr
              key={r.team}
              className="border-border-subtle border-b last:border-b-0"
            >
              <td className="text-text-primary py-2 font-medium wrap-anywhere">
                {r.team}
              </td>
              {/* The rail is a fixed 340px from lg (THR-376 r2 G1), so the
                  member count keeps its own column at every width; the old
                  narrow-rail stacking (1024-1279px) is no longer needed (D2). */}
              <td className="text-text-muted py-2 font-mono text-xs whitespace-nowrap">
                {membersLabel}
              </td>
              <td className="py-2">
                <Sparkline data={empty ? [] : r.sparkline} width={SPARK_W} variant={tier} />
              </td>
              <td
                className={`${empty ? 'text-text-muted' : ACCEPTANCE_TEXT[tier]} py-2 text-right font-mono text-xs`}
              >
                {empty ? '—' : `${r.acceptance_pct}%`}
              </td>
              <td className="text-text-muted py-2 pl-3 text-right font-mono text-xs">
                {empty ? '—' : (
                  <>
                    {trendSign}
                    {trendValue || '—'}
                  </>
                )}
              </td>
            </tr>
          );
        })}
      </tbody>
    </table>
  );
}
