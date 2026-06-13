/**
 * "Top token threads (window)" — read-only cost-oversight card.
 *
 * Self-contained: fetches its own data over the existing
 * `GET /tokens?group_by=thread` route (NOT DashboardSummaryResponse) and ranks
 * client-side. Answers the founder's "which threads are burning the most
 * tokens right now?" at a glance. Spec: token-usage visibility surface §5.
 *
 * Churn invariant: the bar length and the rank key are `totalTokens`
 * (= input + output + reasoning). Cache reads are shown as a muted secondary
 * number and never enter the bar, the sort, or the total. Model labels follow
 * the same precedence as the CLI (see ../topTokens.ts).
 *
 * Bars use SVG <rect> (numeric width attrs) — the design system bans inline
 * `style` and arbitrary Tailwind values in features, and SVG presentation
 * attributes are the sanctioned escape for proportional bars (cf. Heartbeat).
 */
import { useMemo, useState } from 'react';
import { useTopThreadTokens } from '@/hooks/tokens';
import { cn } from '@/lib/utils';
import { toTopRows } from '../topTokens';

const WINDOWS = [
  { label: '24h', ms: 24 * 60 * 60 * 1000 },
  { label: '7d', ms: 7 * 24 * 60 * 60 * 1000 },
  { label: '30d', ms: 30 * 24 * 60 * 60 * 1000 },
] as const;

const TOP_N = 8;
const BAR_W = 96; // px — SVG viewport for the churn bar
const BAR_H = 8;

export function TopTokenThreadsPanel(): JSX.Element {
  const [winIdx, setWinIdx] = useState(1); // default 7d
  const win = WINDOWS[winIdx];
  // Stable per window selection — recomputing the `since` string every render
  // would churn the query key and refetch in a loop.
  const since = useMemo(
    () => new Date(Date.now() - win.ms).toISOString(),
    [win.ms],
  );
  const q = useTopThreadTokens({ since });

  const rows = toTopRows(q.data ?? [], TOP_N);
  const max = Math.max(...rows.map((r) => r.totalTokens), 1);

  return (
    <section className="border-border-subtle bg-surface-sunken rounded-md border p-4">
      <header className="mb-3 flex items-baseline justify-between">
        <h2 className="text-text-muted text-xs font-medium tracking-wider uppercase">
          Top token threads
        </h2>
        <div className="flex gap-1 font-mono text-xs" role="group" aria-label="Window">
          {WINDOWS.map((w, i) => (
            <button
              key={w.label}
              type="button"
              onClick={() => setWinIdx(i)}
              aria-pressed={i === winIdx}
              className={cn(
                'rounded px-1.5 py-0.5',
                i === winIdx
                  ? 'bg-surface-raised text-text-primary'
                  : 'text-text-muted hover:text-text-primary',
              )}
            >
              {w.label}
            </button>
          ))}
        </div>
      </header>

      {q.isLoading ? (
        <p className="text-text-muted text-sm">Loading…</p>
      ) : q.isError ? (
        <p className="text-feedback-danger text-sm">Failed to load token usage.</p>
      ) : rows.length === 0 ? (
        <p className="text-text-muted text-sm">No token usage in window.</p>
      ) : (
        <ul className="space-y-1.5 font-mono text-xs">
          {rows.map((r) => (
            <li key={r.threadId} className="flex items-center gap-2">
              <span className="text-text-primary w-24 shrink-0 truncate" title={r.threadId}>
                {r.threadId}
              </span>
              <span className="text-text-muted w-28 shrink-0 truncate" title={r.modelLabel}>
                {r.modelLabel}
              </span>
              <svg
                width={BAR_W}
                height={BAR_H}
                className="shrink-0"
                aria-hidden="true"
              >
                <rect
                  x={0}
                  y={0}
                  width={Math.max((r.totalTokens / max) * BAR_W, 1)}
                  height={BAR_H}
                  rx={1}
                  className="fill-accent"
                />
              </svg>
              <span className="text-text-primary ml-auto tabular-nums">
                {r.totalTokens.toLocaleString()}
              </span>
              <span
                className="text-text-muted w-20 shrink-0 text-right tabular-nums"
                title="cache reads — never counted toward churn"
              >
                {r.cacheReadTokens.toLocaleString()}
                <span className="ml-1">cache</span>
              </span>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
