/**
 * Heartbeat — histogram of orchestration steps per hour. THR-376 r2 G8: the
 * design's 16 bars x 5px with a 3px gap — the 16 most recent hourly buckets,
 * oldest left, ending at the current hour (`nowIdx`).
 *
 * Tier colors come from the response model (ok / warn / bad), not from
 * client-side classification. Zero-step hours render as a flat neutral
 * border-color bar — visually present so the rhythm of the day reads.
 *
 * Feature-local because no other surface needs a 24-hour heartbeat.
 * Promote to design-system/patterns/ on third use.
 *
 * The 3px gap is `gap-0.75` (Tailwind's spacing scale), not an arbitrary value.
 */
import type { HeartbeatBucket } from '@/lib/api/types';
import { cn } from '@/lib/utils';
import { useTranslation } from '@/hooks/i18n';

interface HeartbeatProps {
  data: HeartbeatBucket[];
  nowIdx?: number;
}


const BARS = 16;
const BAR_W = 5;

/** The last BARS buckets in time order, ending at the `nowIdx` hour. */
function recentBuckets(data: HeartbeatBucket[], nowIdx?: number): HeartbeatBucket[] {
  if (nowIdx === undefined) return data.slice(-BARS);
  const byHour = new Map(data.map((b) => [b.hour, b]));
  const out: HeartbeatBucket[] = [];
  for (let k = BARS - 1; k >= 0; k -= 1) {
    const b = byHour.get((nowIdx - k + 24) % 24);
    if (b) out.push(b);
  }
  return out;
}

export function Heartbeat({ data, nowIdx }: HeartbeatProps): JSX.Element {
  const { t } = useTranslation();
  const bars = recentBuckets(data, nowIdx);
  // Bar height is driven by the combined "activity-or-outcome" signal so an
  // hour with cascade-fails but no audit activity (failed > 0, steps == 0)
  // still paints a visible red bar — `failed` is sourced from terminal task
  // outcomes and may exceed `steps`.
  const heightSignal = (b: HeartbeatBucket): number =>
    Math.max(b.steps, b.failed ?? 0);
  const maxSignal = Math.max(...bars.map(heightSignal), 1);
  const H = 36;
  // 16 bars at 5px + 3px gaps are 125px wide, which fits the 300px rail
  // content at every width (THR-376 D4: no Home card overflow at 1100).
  return (
    <div
      className="flex h-9 items-end gap-0.75"
      aria-label={t('dashboard.heartbeat.label')}
    >
      {bars.map((b) => {
        const signal = heightSignal(b);
        const totalH = (signal / maxSignal) * H;
        const failed = b.failed ?? 0;
        const failedRatio = signal > 0 ? Math.min(failed / signal, 1) : 0;
        const failedH = totalH * failedRatio;
        const okH = totalH - failedH;
        return (
          <svg
            key={b.hour}
            width={BAR_W}
            height={H}
            className={cn(
              'overflow-visible',
              b.hour === nowIdx ? 'opacity-100' : 'opacity-70',
            )}
          >
            {signal === 0 ? (
              <rect x={0} y={H - 2} width={BAR_W} height={2} rx={1} className="fill-border-default" />
            ) : (
              <>
                {okH > 0 && (
                  <rect x={0} y={H - totalH} width={BAR_W} height={okH} rx={1} className="fill-tier-green" />
                )}
                {failedH > 0 && (
                  <rect x={0} y={H - totalH + okH} width={BAR_W} height={failedH} rx={1} className="fill-tier-red" />
                )}
              </>
            )}
          </svg>
        );
      })}
    </div>
  );
}
