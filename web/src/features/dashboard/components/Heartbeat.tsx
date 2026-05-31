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

const TIER_FILL: Record<HeartbeatBucket['tier'], string> = {
  ok: 'fill-tier-green',
  warn: 'fill-tier-yellow',
  bad: 'fill-tier-red',
};

export function Heartbeat({ data, nowIdx }: HeartbeatProps): JSX.Element {
  const maxSteps = Math.max(...data.map((b) => b.steps), 1);
  return (
    <div
      className="flex h-9 items-end gap-1"
      aria-label="Today's hourly activity"
    >
      {data.map((b, i) => {
        const h = (b.steps / maxSteps) * 36;
        return (
          <svg
            key={b.hour}
            width={6}
            height={36}
            className={cn(
              'overflow-visible',
              i === nowIdx ? 'opacity-100' : 'opacity-70',
            )}
          >
            <rect
              x={0}
              y={36 - h}
              width={6}
              height={h}
              rx={1}
              className={
                b.steps === 0 ? 'fill-border-default' : TIER_FILL[b.tier]
              }
            />
          </svg>
        );
      })}
    </div>
  );
}
