/**
 * StatValue — overflow-safe DISPLAY-METRIC numeral (THR-099 number-overflow
 * fix).
 *
 * Renders a compact, locale-aware token/count in a `tabular-nums`, no-wrap,
 * reserved-width, no-clip container while preserving the FULL precision via the
 * `title` tooltip (`value.toLocaleString()`) — the exact figure is never lost.
 * This is the single primitive every token/cache/sum/count render composes so
 * the "compact + reserved width + title" contract can't be forgotten per site.
 *
 * DISPLAY METRICS ONLY. Exact identifiers (task/thread/job/PR IDs, ports,
 * hashes, exit codes) must NEVER be routed through StatValue — routing an id
 * here would compact it (a port 8765 → "8.8K"). Identifiers keep IdBadge /
 * truncate+title.
 *
 * Layout:
 *   - `align="right"` (default) — an inline-block, right-aligned, min-width
 *     reserved box for table/panel cells (the founder-overflow surfaces). The
 *     reserved width stops column jitter and gives the numeral a stable box.
 *   - `align="inline"` — no reserved box / right pad, for centered hero tiles
 *     (Dashboard TODAY) where the caller controls alignment via `className`.
 *     No-clip + no-wrap + title safety still apply.
 */
import type { ReactNode } from 'react';
import { formatTokens, formatCount } from '@/lib/format';
import { cn } from '@/lib/utils';

interface StatValueProps {
  /** The raw numeric metric. Full precision is preserved in the title. */
  value: number;
  /** `tokens` → compact K/M (default); `count` → exact grouped integer. */
  format?: 'tokens' | 'count';
  /** Optional muted trailing label, e.g. "cache" or "tok". */
  suffix?: ReactNode;
  /** `right` (default) reserves a right-aligned box; `inline` for hero tiles. */
  align?: 'right' | 'inline';
  className?: string;
}

export function StatValue({
  value,
  format = 'tokens',
  suffix,
  align = 'right',
  className,
}: StatValueProps): JSX.Element {
  const text = format === 'count' ? formatCount(value) : formatTokens(value);
  return (
    <span
      className={cn(
        'whitespace-nowrap tabular-nums',
        align === 'right' && 'inline-block min-w-16 pr-1 text-right',
        className,
      )}
      title={value.toLocaleString()}
    >
      {text}
      {suffix != null && (
        <span className="text-text-disabled ml-1.5">{suffix}</span>
      )}
    </span>
  );
}

export const meta = {
  name: 'StatValue',
  layer: 'pattern',
  import: '@/design-system/patterns/StatValue',
  variants: { align: ['right', 'inline'], format: ['tokens', 'count'] },
  consumes: ['components.stat_value'],
  example: "<StatValue value={3707054} format='tokens' suffix='cache' />",
} as const;
