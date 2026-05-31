/**
 * Sparkline — minimal SVG polyline for trend data. Pure prop-driven.
 *
 * Used by the founder dashboard (Org pulse table) and by the Agents
 * scorecard table. No axes, no labels — it's a glyph of motion.
 *
 * Empty `data` renders an empty SVG of the requested size (consumers
 * still get a stable reservation in their layout).
 */
import { cn } from '@/lib/utils';

type SparklineVariant = 'default' | 'green' | 'yellow' | 'red';

interface SparklineProps {
  data: number[];
  width?: number;
  height?: number;
  variant?: SparklineVariant;
}

const STROKE_CLASS: Record<SparklineVariant, string> = {
  default: 'stroke-text-secondary',
  green: 'stroke-tier-green',
  yellow: 'stroke-tier-yellow',
  red: 'stroke-tier-red',
};

export function Sparkline({
  data,
  width = 64,
  height = 16,
  variant = 'default',
}: SparklineProps): JSX.Element {
  if (data.length === 0) {
    return <svg width={width} height={height} aria-hidden="true" />;
  }
  const minY = Math.min(...data, 0);
  const maxY = Math.max(...data, 1);
  const range = maxY - minY || 1;
  const stepX = data.length === 1 ? 0 : width / (data.length - 1);
  const points = data
    .map((v, i) => {
      const x = i * stepX;
      const y = height - ((v - minY) / range) * height;
      return `${x.toFixed(1)},${y.toFixed(1)}`;
    })
    .join(' ');

  return (
    <svg width={width} height={height} aria-hidden="true">
      <polyline
        points={points}
        fill="none"
        strokeWidth={1.5}
        className={cn('stroke-current', STROKE_CLASS[variant])}
      />
    </svg>
  );
}

export const meta = {
  name: "Sparkline",
  layer: "pattern",
  import: "@/design-system/patterns/Sparkline",
  variants: { variant: ["default", "green", "yellow", "red"] },
  consumes: ["typography.mono_sm", "colors.semantic.dark.tier"],
  example: "<Sparkline data={[0.8, 0.84, 0.78, 0.82, 0.86]} />",
} as const;
