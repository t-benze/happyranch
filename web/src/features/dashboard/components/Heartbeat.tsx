/**
 * Heartbeat — 24-bar histogram of orchestration steps per hour.
 *
 * Tier colors come from the response model (ok / warn / bad), not from
 * client-side classification. Zero-step hours render as a flat neutral
 * border-color bar — visually present so the rhythm of the day reads.
 *
 * Feature-local because no other surface needs a 24-hour heartbeat.
 * Promote to design-system/patterns/ on third use.
 *
 * Lint note: the design spec called for `gap-[3px]`; arbitrary Tailwind
 * values are blocked in `src/features/` by `tailwindcss/no-arbitrary-value`
 * (see web/eslint.config.js). Substituted the nearest token `gap-1` (4px).
 */
import type { HeartbeatBucket } from '@/lib/api/types';
import { cn } from '@/lib/utils';

interface HeartbeatProps {
  data: HeartbeatBucket[];
  nowIdx?: number;
}


export function Heartbeat({ data, nowIdx }: HeartbeatProps): JSX.Element {
  // Bar height is driven by the combined "activity-or-outcome" signal so an
  // hour with cascade-fails but no audit activity (failed > 0, steps == 0)
  // still paints a visible red bar — `failed` is sourced from terminal task
  // outcomes and may exceed `steps`.
  const heightSignal = (b: HeartbeatBucket): number =>
    Math.max(b.steps, b.failed ?? 0);
  const maxSignal = Math.max(...data.map(heightSignal), 1);
  const H = 36;
  return (
    <div
      className="flex h-9 items-end gap-1"
      aria-label="Today's hourly activity"
    >
      {data.map((b, i) => {
        const signal = heightSignal(b);
        const totalH = (signal / maxSignal) * H;
        const failed = b.failed ?? 0;
        const failedRatio = signal > 0 ? Math.min(failed / signal, 1) : 0;
        const failedH = totalH * failedRatio;
        const okH = totalH - failedH;
        return (
          <svg
            key={b.hour}
            width={6}
            height={H}
            className={cn(
              'overflow-visible',
              i === nowIdx ? 'opacity-100' : 'opacity-70',
            )}
          >
            {signal === 0 ? (
              <rect x={0} y={H - 2} width={6} height={2} rx={1} className="fill-border-default" />
            ) : (
              <>
                {okH > 0 && (
                  <rect x={0} y={H - totalH} width={6} height={okH} rx={1} className="fill-tier-green" />
                )}
                {failedH > 0 && (
                  <rect x={0} y={H - totalH + okH} width={6} height={failedH} rx={1} className="fill-tier-red" />
                )}
              </>
            )}
          </svg>
        );
      })}
    </div>
  );
}
