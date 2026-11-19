/**
 * HealthPage — Runtime Health cockpit (THR-061 Slice 10 / #302).
 *
 * Renders the daemon-global operational metrics from the two EXISTING routes:
 *   - GET /api/v1/metrics          (live snapshot + pull-gauges)
 *   - GET /api/v1/metrics/history  (persisted rows, newest-first)
 *
 * Honesty fence: every value below is a field the routes actually return
 * (see runtime/daemon/routes/metrics.py + metrics_store.py). No invented
 * metric, badge, dollar, or status. Latencies are seconds server-side and
 * are rendered as humanized durations (ms below 1 s, else seconds; THR-376 r2
 * H3) with the exact ms kept in a title. Pasture tokens only — zero raw hex.
 */
import { useMemo, useState, useCallback } from 'react';
import {
  useMetrics,
  useMetricsHistory,
  type MetricsSnapshot,
  type LoopStats,
  type HttpRouteStats,
  type ParsedHistoryRow,
} from '@/hooks/metrics';
import { PageTitle } from '@/design-system/patterns/PageTitle';
import { StatusBadge } from '@/design-system/patterns/StatusBadge';
import { TopbarStatus } from '@/design-system/layouts/AppShell/TopbarSlots';
import { Sparkline } from '@/design-system/patterns/Sparkline';
import { SegmentedControl } from '@/design-system/primitives/SegmentedControl';
import { formatCount } from '@/lib/format';
import { cn } from '@/lib/utils';
import { useTranslation } from '@/hooks/i18n';
import './health.css';
import type { Locale, MessageKey, MessageParams } from '@/lib/i18n';

/** Mirrors `useMetrics()` refetchInterval (15_000 ms) in src/hooks/metrics.ts. */
const METRICS_POLL_SECONDS = 15;

type Translate = (key: MessageKey, params?: MessageParams) => string;

/* ------------------------------------------------------------------ */
/*  History window (drives the /metrics/history `since` bound)          */
/* ------------------------------------------------------------------ */

const WINDOWS = [
  { label: 'health.window.1h', ms: 60 * 60 * 1000 },
  { label: 'health.window.24h', ms: 24 * 60 * 60 * 1000 },
  { label: 'health.window.7d', ms: 7 * 24 * 60 * 60 * 1000 },
] as const satisfies readonly { label: MessageKey; ms: number }[];

const STORAGE_KEY = 'hr-health-window';

function loadWindowIdx(): number {
  try {
    const v = localStorage.getItem(STORAGE_KEY);
    if (v != null) {
      const n = parseInt(v, 10);
      if (n >= 0 && n < WINDOWS.length) return n;
    }
  } catch {
    /* storage unavailable */
  }
  return 1; // default 24h
}

function saveWindowIdx(idx: number): void {
  try {
    localStorage.setItem(STORAGE_KEY, String(idx));
  } catch {
    /* storage unavailable */
  }
}

/* ------------------------------------------------------------------ */
/*  Format helpers — honest renderers, never a guessed value           */
/* ------------------------------------------------------------------ */

/** Human uptime from seconds, e.g. "2d 3h", "3h 14m", "5m 12s", "42s". */
export function fmtUptime(seconds: number, t: Translate): string {
  const s = Math.max(0, Math.floor(seconds));
  const d = Math.floor(s / 86_400);
  const h = Math.floor((s % 86_400) / 3600);
  const m = Math.floor((s % 3600) / 60);
  const sec = s % 60;
  if (d > 0) return t('health.uptime.days', { days: d, hours: h });
  if (h > 0) return t('health.uptime.hours', { hours: h, minutes: m });
  if (m > 0) return t('health.uptime.minutes', { minutes: m, seconds: sec });
  return t('health.uptime.seconds', { seconds: sec });
}

/**
 * Latency seconds → humanized, locale-aware duration (THR-376 r2 H3).
 * Below 1 s it stays in ms (≥100 ms whole, else one decimal); from 1 s up it
 * reads in seconds with one decimal, e.g. "72.0 s" instead of "72031 ms".
 * Null (zero samples) → em dash.
 */
export function fmtMs(latencySeconds: number | null | undefined, t: Translate, locale: Locale): string {
  if (latencySeconds == null) return '—';
  const ms = latencySeconds * 1000;
  if (ms >= 1000) {
    const value = new Intl.NumberFormat(locale, { minimumFractionDigits: 1, maximumFractionDigits: 1 }).format(ms / 1000);
    return t('health.duration.seconds', { value });
  }
  const digits = ms >= 100 ? 0 : 1;
  const value = new Intl.NumberFormat(locale, { minimumFractionDigits: digits, maximumFractionDigits: digits }).format(ms);
  return t('health.duration.ms', { value });
}

/** Exact milliseconds for the duration cell's title (THR-376 r2 H3). */
export function fmtExactMs(latencySeconds: number | null | undefined, t: Translate, locale: Locale): string | undefined {
  if (latencySeconds == null) return undefined;
  const value = new Intl.NumberFormat(locale, { maximumFractionDigits: 3 }).format(latencySeconds * 1000);
  return t('health.duration.ms', { value });
}

/** Split a daemon route label "METHOD /path" (runtime/daemon/metrics.py
 *  route_template_label) into its method and route (THR-376 r2 H1). A label
 *  without a leading upper-case method renders as a bare route. */
function splitRoute(label: string): { method: string | null; path: string } {
  const m = /^([A-Z]+) (.+)$/.exec(label);
  return m ? { method: m[1]!, path: m[2]! } : { method: null, path: label };
}

// The local `fmtInt` (raw toLocaleString) was folded into the canonical
// `formatCount` (@/lib/format) — THR-099 number-overflow consolidation. Health
// counts are exact small bounded integers, so formatCount keeps them precise
// and grouped (never compacted).
const fmtInt = formatCount;

/** Compact relative time from an ISO string, e.g. "12s ago", "3m ago". */
export function fmtRelTime(iso: string, t: Translate, now: number = Date.now()): string {
  const at = Date.parse(iso);
  if (Number.isNaN(at)) return iso;
  const deltaS = Math.max(0, Math.round((now - at) / 1000));
  if (deltaS < 60) return t('health.ago.seconds', { count: deltaS });
  const m = Math.floor(deltaS / 60);
  if (m < 60) return t('health.ago.minutes', { count: m });
  const h = Math.floor(m / 60);
  if (h < 24) return t('health.ago.hours', { count: h });
  return t('health.ago.days', { count: Math.floor(h / 24) });
}

const AGGREGATE_KEY = '__all__';

/* ------------------------------------------------------------------ */
/*  Summary stat cards — live pull-gauges + uptime                     */
/* ------------------------------------------------------------------ */

interface StatDef {
  key: string;
  label: MessageKey;
  value: (s: MetricsSnapshot, t: Translate) => string;
  hint?: MessageKey;
}

const STATS: StatDef[] = [
  {
    key: 'uptime',
    label: 'health.metric.uptime',
    value: (s, t) => fmtUptime(s.uptime_seconds, t),
    hint: 'health.metric.uptimeHint',
  },
  {
    key: 'tasks',
    label: 'health.metric.tasks',
    value: (s) => fmtInt(s.tasks.pending_and_in_flight),
    hint: 'health.metric.tasksHint',
  },
  {
    key: 'jobs',
    label: 'health.metric.jobs',
    value: (s) => fmtInt(s.jobs_in_flight),
    hint: 'health.metric.jobsHint',
  },
  {
    key: 'sessions',
    label: 'health.metric.sessions',
    value: (s) => fmtInt(s.executor_sessions_active),
    hint: 'health.metric.sessionsHint',
  },
  {
    key: 'queue',
    label: 'health.metric.queue',
    value: (s) => fmtInt(s.run_step_queue_depth),
    hint: 'health.metric.queueHint',
  },
];

function StatCard({
  label,
  value,
  hint,
}: {
  label: string;
  value: string;
  hint?: string;
}): JSX.Element {
  return (
    <div className="bg-surface-raised border-border-default shadow-pasture-sm rounded-lg border p-4">
      {/* THR-376 r2 H2: 11.5px sentence-case label; 27px Newsreader value
          (.health-stat-value in health.css — no 27px type token exists). */}
      <p className="text-text-secondary text-task-meta font-medium">{label}</p>
      <p className="health-stat-value font-display text-text-primary mt-2 font-medium tabular-nums">{value}</p>
      {hint && <p className="text-text-muted text-2xs mt-1">{hint}</p>}
    </div>
  );
}

function SummaryCards({
  snapshot,
  loading,
}: {
  snapshot: MetricsSnapshot | undefined;
  loading: boolean;
}): JSX.Element {
  const { t } = useTranslation();
  return (
    <div className="grid grid-cols-2 gap-4 sm:grid-cols-3 lg:grid-cols-5">
      {STATS.map((stat) =>
        loading || !snapshot ? (
          <div
            key={stat.key}
            className="bg-surface-raised border-border-default shadow-pasture-sm rounded-lg border p-4"
          >
            <div className="animate-pulse space-y-2">
              <div className="bg-surface-raised h-3 w-20 rounded-sm" />
              <div className="bg-surface-raised h-12 w-16 rounded-sm" />
            </div>
          </div>
        ) : (
          <StatCard
            key={stat.key}
            label={t(stat.label)}
            value={stat.value(snapshot, t)}
            hint={stat.hint ? t(stat.hint) : undefined}
          />
        ),
      )}
    </div>
  );
}

/* ------------------------------------------------------------------ */
/*  Loops table                                                        */
/* ------------------------------------------------------------------ */

interface LoopRow extends LoopStats {
  name: string;
}

function LoopsCard({
  loops,
  loading,
}: {
  loops: Record<string, LoopStats> | undefined;
  loading: boolean;
}): JSX.Element {
  const { t, locale } = useTranslation();
  const rows: LoopRow[] = useMemo(() => {
    if (!loops) return [];
    return Object.entries(loops)
      .map(([name, l]) => ({ name, ...l }))
      .sort((a, b) => a.name.localeCompare(b.name));
  }, [loops]);

  return (
    <section className="bg-surface-raised border-border-default shadow-pasture-sm rounded-lg border p-4">
      <h2 className="text-text-secondary text-body-sm mb-3 font-semibold">
        {t('health.loops.title')}
      </h2>
      {loading ? (
        <div className="animate-pulse space-y-2">
          {[1, 2, 3].map((i) => (
            <div key={i} className="bg-surface-raised h-8 rounded-sm" />
          ))}
        </div>
      ) : rows.length === 0 ? (
        <p className="text-text-muted text-sm">{t('health.loops.empty')}</p>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full text-left font-mono text-xs">
            <thead>
              {/* THR-376 r2 H3: 10px uppercase head band on surface-2 (sunken). */}
              <tr className="bg-surface-sunken text-text-muted border-border-default text-2xs border-b font-sans tracking-wider uppercase">
                <th className="px-3 py-2 font-semibold">{t('health.loops.column.loop')}</th>
                <th className="py-2 pr-3 text-right font-semibold">{t('health.loops.column.interval')}</th>
                <th className="py-2 pr-3 text-right font-semibold">{t('health.loops.column.lastDuration')}</th>
                <th className="py-2 pr-3 text-right font-semibold">{t('health.loops.column.lastTick')}</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((r) => (
                <tr key={r.name} className="border-border-default border-b last:border-0">
                  <td className="text-text-primary px-3 py-2">{r.name}</td>
                  <td className="text-text-muted py-2 pr-3 text-right tabular-nums">
                    {t('health.loops.interval', { value: r.interval_seconds })}
                  </td>
                  <td
                    className="text-text-primary py-2 pr-3 text-right whitespace-nowrap tabular-nums"
                    title={fmtExactMs(r.last_duration_seconds, t, locale)}
                  >
                    {fmtMs(r.last_duration_seconds, t, locale)}
                  </td>
                  <td className="text-text-muted py-2 pr-3 text-right tabular-nums">
                    {fmtRelTime(r.last_tick_iso, t)}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}

/* ------------------------------------------------------------------ */
/*  HTTP latency table                                                 */
/* ------------------------------------------------------------------ */

interface HttpRow extends HttpRouteStats {
  route: string;
  isAggregate: boolean;
}

function HttpCard({
  http,
  loading,
}: {
  http: Record<string, HttpRouteStats> | undefined;
  loading: boolean;
}): JSX.Element {
  const { t, locale } = useTranslation();
  const rows: HttpRow[] = useMemo(() => {
    if (!http) return [];
    const entries = Object.entries(http).map(
      ([route, h]): HttpRow => ({
        route,
        isAggregate: route === AGGREGATE_KEY,
        ...h,
      }),
    );
    // Aggregate bucket first, then per-route by sample count desc.
    return entries.sort((a, b) => {
      if (a.isAggregate !== b.isAggregate) return a.isAggregate ? -1 : 1;
      return b.count - a.count || a.route.localeCompare(b.route);
    });
  }, [http]);

  return (
    <section className="bg-surface-raised border-border-default shadow-pasture-sm rounded-lg border p-4">
      <h2 className="text-text-secondary text-body-sm mb-3 font-semibold">
        {t('health.http.title')}
      </h2>
      {loading ? (
        <div className="animate-pulse space-y-2">
          {[1, 2, 3, 4].map((i) => (
            <div key={i} className="bg-surface-raised h-8 rounded-sm" />
          ))}
        </div>
      ) : rows.length === 0 ? (
        <p className="text-text-muted text-sm">{t('health.http.empty')}</p>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full text-left font-mono text-xs">
            <thead>
              {/* THR-376 r2 H3: 10px uppercase head band on surface-2 (sunken). */}
              <tr className="bg-surface-sunken text-text-muted border-border-default text-2xs border-b font-sans tracking-wider uppercase">
                <th className="px-3 py-2 font-semibold">{t('health.http.column.route')}</th>
                <th className="py-2 pr-3 text-right font-semibold">{t('health.http.column.count')}</th>
                <th className="py-2 pr-3 text-right font-semibold">{t('health.http.column.p50')}</th>
                <th className="py-2 pr-3 text-right font-semibold">{t('health.http.column.p95')}</th>
                <th className="py-2 pr-3 text-right font-semibold">{t('health.http.column.max')}</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((r) => {
                const route = splitRoute(r.route);
                return (
                <tr
                  key={r.route}
                  className={cn(
                    'border-border-default border-b last:border-0',
                    r.isAggregate && 'bg-surface-raised/40',
                  )}
                >
                  <td className="px-3 py-2">
                    {r.isAggregate ? (
                      <span className="text-text-primary font-medium">{t('health.http.allRoutes')}</span>
                    ) : (
                      /* THR-376 r2 H1: info-soft method badge + full mono
                         route that wraps instead of truncating (onto its own line under
                         the badge when the column is narrow). */
                      <span className="flex min-w-0 flex-wrap items-start gap-x-2 gap-y-1">
                        {route.method && <StatusBadge tone="info" label={route.method} />}
                        <span className="text-text-primary min-w-16 flex-1 wrap-anywhere" title={r.route}>
                          {route.path}
                        </span>
                      </span>
                    )}
                  </td>
                  <td className="text-text-muted py-2 pr-3 text-right tabular-nums">{fmtInt(r.count)}</td>
                  <td className="text-text-primary py-2 pr-3 text-right whitespace-nowrap tabular-nums" title={fmtExactMs(r.p50, t, locale)}>
                    {fmtMs(r.p50, t, locale)}
                  </td>
                  <td className="text-text-primary py-2 pr-3 text-right whitespace-nowrap tabular-nums" title={fmtExactMs(r.p95, t, locale)}>
                    {fmtMs(r.p95, t, locale)}
                  </td>
                  <td className="text-text-muted py-2 pr-3 text-right whitespace-nowrap tabular-nums" title={fmtExactMs(r.max, t, locale)}>
                    {fmtMs(r.max, t, locale)}
                  </td>
                </tr>
              );
              })}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}

/* ------------------------------------------------------------------ */
/*  History charts — Sparkline trends over persisted snapshots         */
/* ------------------------------------------------------------------ */

interface TrendDef {
  key: string;
  label: MessageKey;
  pick: (s: MetricsSnapshot) => number | null;
  fmt: (v: number, t: Translate, locale: Locale) => string;
  variant: 'default' | 'green' | 'yellow' | 'red';
}

const TRENDS: TrendDef[] = [
  {
    key: 'queue',
    label: 'health.metric.queue',
    pick: (s) => s.run_step_queue_depth,
    fmt: (v) => fmtInt(v),
    variant: 'default',
  },
  {
    key: 'sessions',
    label: 'health.metric.sessions',
    pick: (s) => s.executor_sessions_active,
    fmt: (v) => fmtInt(v),
    variant: 'green',
  },
  {
    key: 'tasks',
    label: 'health.metric.tasks',
    pick: (s) => s.tasks?.pending_and_in_flight ?? null,
    fmt: (v) => fmtInt(v),
    variant: 'default',
  },
  {
    key: 'jobs',
    label: 'health.metric.jobs',
    pick: (s) => s.jobs_in_flight,
    fmt: (v) => fmtInt(v),
    variant: 'yellow',
  },
  {
    key: 'p95',
    label: 'health.metric.p95',
    pick: (s) => s.http?.[AGGREGATE_KEY]?.p95 ?? null,
    fmt: (v, t, locale) => fmtMs(v, t, locale),
    variant: 'default',
  },
];

/** Extract the chronological (oldest→newest) numeric series for a trend from
 *  parsed history rows. Rows the server couldn't parse, or snapshots missing
 *  the field, are skipped — never zero-filled into a fake data point. */
function trendSeries(rowsNewestFirst: ParsedHistoryRow[], pick: TrendDef['pick']): number[] {
  const chronological = [...rowsNewestFirst].reverse();
  const out: number[] = [];
  for (const row of chronological) {
    if (!row.snapshot) continue;
    const v = pick(row.snapshot);
    if (v == null || Number.isNaN(v)) continue;
    out.push(v);
  }
  return out;
}

function TrendCard({
  def,
  series,
}: {
  def: TrendDef;
  series: number[];
}): JSX.Element {
  const { t, locale } = useTranslation();
  const current = series.length > 0 ? series[series.length - 1]! : null;
  const peak = series.length > 0 ? Math.max(...series) : null;
  return (
    <div className="bg-surface-raised border-border-default shadow-pasture-sm rounded-lg border p-4">
      <div className="flex items-baseline justify-between gap-2">
        <p className="text-text-secondary text-task-meta font-medium">{t(def.label)}</p>
        <p className="text-text-muted text-2xs tabular-nums">
          {peak != null ? t('health.trends.peak', { value: def.fmt(peak, t, locale) }) : ''}
        </p>
      </div>
      <p className="font-display text-h3 text-text-primary mt-2 font-medium tabular-nums">
        {current != null ? def.fmt(current, t, locale) : '—'}
      </p>
      <div className="mt-3">
        {series.length >= 2 ? (
          <Sparkline data={series} width={220} height={40} variant={def.variant} />
        ) : (
          <p className="text-text-muted text-2xs">{t('health.trends.notEnough')}</p>
        )}
      </div>
    </div>
  );
}

function HistorySection({
  rows,
  loading,
  error,
  windowLabel,
}: {
  rows: ParsedHistoryRow[] | undefined;
  loading: boolean;
  error: boolean;
  windowLabel: string;
}): JSX.Element {
  const { t } = useTranslation();
  const seriesByKey = useMemo(() => {
    const map: Record<string, number[]> = {};
    for (const def of TRENDS) map[def.key] = trendSeries(rows ?? [], def.pick);
    return map;
  }, [rows]);

  const sampleCount = rows?.length ?? 0;

  return (
    <section>
      <div className="mb-3 flex items-center justify-between gap-2">
        <h2 className="text-text-secondary text-body-sm font-semibold">
          {t('health.trends.title', { window: windowLabel })}
        </h2>
        {!loading && !error && (
          <span className="text-text-muted text-2xs tabular-nums">
            {t('health.trends.snapshots', { count: sampleCount })}
          </span>
        )}
      </div>
      {error ? (
        <div className="bg-surface-raised border-border-default shadow-pasture-sm rounded-lg border p-8">
          <p className="text-feedback-danger text-sm">{t('health.trends.error')}</p>
        </div>
      ) : loading ? (
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3">
          {[1, 2, 3].map((i) => (
            <div
              key={i}
              className="bg-surface-raised border-border-default shadow-pasture-sm rounded-lg border p-4"
            >
              <div className="animate-pulse space-y-3">
                <div className="bg-surface-raised h-3 w-24 rounded-sm" />
                <div className="bg-surface-raised h-12 w-16 rounded-sm" />
                <div className="bg-surface-raised h-10 w-full rounded-sm" />
              </div>
            </div>
          ))}
        </div>
      ) : sampleCount === 0 ? (
        <div className="bg-surface-raised border-border-default shadow-pasture-sm rounded-lg border p-8">
          <p className="text-text-muted text-sm">{t('health.trends.empty')}</p>
        </div>
      ) : (
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3">
          {TRENDS.map((def) => (
            <TrendCard key={def.key} def={def} series={seriesByKey[def.key] ?? []} />
          ))}
        </div>
      )}
    </section>
  );
}

/* ------------------------------------------------------------------ */
/*  Window toggle (SegmentedControl, THR-376 PR8)                      */
/* ------------------------------------------------------------------ */

function WindowToggle({
  winIdx,
  onChange,
}: {
  winIdx: number;
  onChange: (i: number) => void;
}): JSX.Element {
  const { t } = useTranslation();
  return (
    <SegmentedControl
      aria-label={t('health.window.group')}
      value={String(winIdx)}
      onValueChange={(v) => onChange(Number(v))}
      options={WINDOWS.map((w, i) => ({ value: String(i), label: t(w.label) }))}
    />
  );
}

/* ------------------------------------------------------------------ */
/*  Main page                                                          */
/* ------------------------------------------------------------------ */

export function HealthPage(): JSX.Element {
  const { t } = useTranslation();
  const [winIdx, setWinIdx] = useState<number>(loadWindowIdx);
  const win = WINDOWS[winIdx]!;
  const since = useMemo(() => new Date(Date.now() - win.ms).toISOString(), [win.ms]);

  const liveQ = useMetrics();
  const historyQ = useMetricsHistory({ since });

  const onChangeWindow = useCallback((i: number) => {
    setWinIdx(i);
    saveWindowIdx(i);
  }, []);

  const snapshot = liveQ.data;

  return (
    <div className="bg-surface-canvas h-full overflow-y-auto">
      {/* THR-376 r2 A2: AppBar page-status chip. Shown only while the live
          snapshot is loaded and not failing; the interval is useMetrics()'s
          existing refetchInterval (15_000 ms, src/hooks/metrics.ts). */}
      {snapshot && !liveQ.isError && (
        <TopbarStatus>
          <StatusBadge tone="positive" dot label={t('health.topbar.live', { seconds: METRICS_POLL_SECONDS })} />
        </TopbarStatus>
      )}
      {/* a-health hl-wrap: 1120 centered cap (THR-099 Slice 7). */}
      <div className="max-w-content-wide mx-auto p-8">
        <header className="mb-8">
          <PageTitle
            title={t('health.title')}
            lede={t('health.meta')}
            actions={<WindowToggle winIdx={winIdx} onChange={onChangeWindow} />}
          />
        </header>

        {liveQ.isError && (
          <div className="border-feedback-danger/30 bg-feedback-danger/5 mb-8 rounded-lg border p-4">
            <p className="text-feedback-danger text-sm">{t('health.liveError')}</p>
          </div>
        )}

        {/* Summary cards */}
        <div className="mb-8">
          <SummaryCards snapshot={snapshot} loading={liveQ.isLoading} />
        </div>

        {/* Loop + HTTP tables */}
        <div className="mb-8 grid grid-cols-1 gap-4 lg:grid-cols-2">
          <LoopsCard loops={snapshot?.loops} loading={liveQ.isLoading} />
          <HttpCard http={snapshot?.http} loading={liveQ.isLoading} />
        </div>

        {/* History trends */}
        <HistorySection
          rows={historyQ.data}
          loading={historyQ.isLoading}
          error={historyQ.isError}
          windowLabel={t(win.label)}
        />
      </div>
    </div>
  );
}
