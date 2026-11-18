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
import { StatValue } from '@/design-system/patterns/StatValue';
import { SegmentedControl } from '@/design-system/primitives/SegmentedControl';
import { useTranslation } from '@/hooks/i18n';
import type { MessageKey } from '@/lib/i18n';
import { NO_THREAD_ID, toTopRows } from '../topTokens';

// `id` is the stable machine identity (React key); `labelKey` is the
// localized display label resolved at render time.
const WINDOWS: ReadonlyArray<{ id: string; labelKey: MessageKey; ms: number }> = [
  { id: '24h', labelKey: 'dashboard.topTokens.window.24h', ms: 24 * 60 * 60 * 1000 },
  { id: '7d', labelKey: 'dashboard.topTokens.window.7d', ms: 7 * 24 * 60 * 60 * 1000 },
  { id: '30d', labelKey: 'dashboard.topTokens.window.30d', ms: 30 * 24 * 60 * 60 * 1000 },
];

const TOP_N = 8;
// Narrower churn bar than the Usage table's: this panel lives in the ~320px
// dashboard rail, so the row must reserve horizontal room for the compact
// total + cache StatValues to stay on-card (THR-099 overflow-11.24.31).
const BAR_W = 48; // px — SVG viewport for the churn bar
const BAR_H = 8;

export function TopTokenThreadsPanel(): JSX.Element {
  const { t, locale } = useTranslation();
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
    <section className="border-border-default bg-surface-raised shadow-pasture-sm rounded-lg border p-6">
      {/* The title and the window control wrap onto two lines when the rail is
          too narrow for both; the control itself never wraps (THR-376 D3). */}
      <header className="mb-4 flex flex-wrap items-center justify-between gap-x-3 gap-y-2">
        <h2 className="text-text-secondary text-xs font-semibold tracking-wider uppercase">
          {t('dashboard.topTokens.title', { window: t(win.labelKey) })}
        </h2>
        <SegmentedControl
          size="sm"
          aria-label={t('dashboard.topTokens.windowGroup')}
          className="shrink-0 flex-nowrap"
          value={win.id}
          onValueChange={(id) => setWinIdx(WINDOWS.findIndex((w) => w.id === id))}
          options={WINDOWS.map((w) => ({ value: w.id, label: t(w.labelKey) }))}
        />
      </header>

      {q.isLoading ? (
        <p className="text-text-muted text-sm">{t('dashboard.topTokens.loading')}</p>
      ) : q.isError ? (
        <p className="text-feedback-danger text-sm">{t('dashboard.topTokens.error')}</p>
      ) : rows.length === 0 ? (
        <p className="text-text-muted text-sm">{t('dashboard.topTokens.empty')}</p>
      ) : (
        <ul className="space-y-1.5 font-mono text-xs">
          {rows.map((r) => {
            const threadLabel =
              r.threadId === NO_THREAD_ID ? t('dashboard.topTokens.noThread') : r.threadId;
            return (
              <li key={r.threadId} className="flex items-center gap-2">
                {/* Narrow lg rail (1024-1279px): the row cannot hold label, bar,
                    total and cache, so the bar is hidden and the label may
                    shrink (truncated, full text in the title) — THR-376 D3. */}
                <span
                  className="text-text-primary w-20 shrink-0 truncate lg:max-xl:w-auto lg:max-xl:min-w-0 lg:max-xl:shrink"
                  title={threadLabel}
                >
                  {threadLabel}
                </span>
                <svg
                  width={BAR_W}
                  height={BAR_H}
                  className="shrink-0 lg:max-xl:hidden"
                  aria-hidden="true"
                >
                  <rect
                    x={0}
                    y={0}
                    width={Math.max((r.totalTokens / max) * BAR_W, 1)}
                    height={BAR_H}
                    rx={1}
                    className="fill-accent-default"
                  />
                </svg>
                <StatValue
                  value={r.totalTokens}
                  align="inline"
                  locale={locale}
                  className="text-text-primary shrink-0"
                />
                <StatValue
                  value={r.cacheReadTokens}
                  suffix={t('dashboard.topTokens.cache')}
                  align="inline"
                  locale={locale}
                  className="text-text-muted shrink-0"
                />
              </li>
            );
          })}
        </ul>
      )}
    </section>
  );
}
