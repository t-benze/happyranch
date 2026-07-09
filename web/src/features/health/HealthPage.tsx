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
 * are rendered in milliseconds. Pasture tokens only — zero raw hex.
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
import { PageHeader } from '@/design-system/patterns/PageHeader';
import { Sparkline } from '@/design-system/patterns/Sparkline';
import { cn } from '@/lib/utils';

/* ------------------------------------------------------------------ */
/*  History window (drives the /metrics/history `since` bound)          */
/* ------------------------------------------------------------------ */

const WINDOWS = [
  { label: '1h', ms: 60 * 60 * 1000 },
  { label: '24h', ms: 24 * 60 * 60 * 1000 },
  { label: '7d', ms: 7 * 24 * 60 * 60 * 1000 },
] as const;

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
export function fmtUptime(seconds: number): string {
  const s = Math.max(0, Math.floor(seconds));
  const d = Math.floor(s / 86_400);
  const h = Math.floor((s % 86_400) / 3600);
  const m = Math.floor((s % 3600) / 60);
  const sec = s % 60;
  if (d > 0) return `${d}d ${h}h`;
  if (h > 0) return `${h}h ${m}m`;
  if (m > 0) return `${m}m ${sec}s`;
  return `${sec}s`;
}

/** Latency seconds → milliseconds label. Null (zero samples) → em dash. */
export function fmtMs(latencySeconds: number | null | undefined): string {
  if (latencySeconds == null) return '—';
  const ms = latencySeconds * 1000;
  if (ms >= 100) return `${Math.round(ms)} ms`;
  return `${ms.toFixed(1)} ms`;
}

function fmtInt(n: number): string {
  return n.toLocaleString();
}

/** Compact relative time from an ISO string, e.g. "12s ago", "3m ago". */
export function fmtRelTime(iso: string, now: number = Date.now()): string {
  const t = Date.parse(iso);
  if (Number.isNaN(t)) return iso;
  const deltaS = Math.max(0, Math.round((now - t) / 1000));
  if (deltaS < 60) return `${deltaS}s ago`;
  const m = Math.floor(deltaS / 60);
  if (m < 60) return `${m}m ago`;
  const h = Math.floor(m / 60);
  if (h < 24) return `${h}h ago`;
  return `${Math.floor(h / 24)}d ago`;
}

const AGGREGATE_KEY = '__all__';

/* ------------------------------------------------------------------ */
/*  Summary stat cards — live pull-gauges + uptime                     */
/* ------------------------------------------------------------------ */

interface StatDef {
  key: string;
  label: string;
  value: (s: MetricsSnapshot) => string;
  hint?: string;
}

const STATS: StatDef[] = [
  { key: 'uptime', label: 'Uptime', value: (s) => fmtUptime(s.uptime_seconds), hint: 'since daemon start' },
  {
    key: 'tasks',
    label: 'Tasks in flight',
    value: (s) => fmtInt(s.tasks.pending_and_in_flight),
    hint: 'pending + in flight',
  },
  { key: 'jobs', label: 'Jobs in flight', value: (s) => fmtInt(s.jobs_in_flight), hint: 'running jobs' },
  {
    key: 'sessions',
    label: 'Active sessions',
    value: (s) => fmtInt(s.executor_sessions_active),
    hint: 'executor sessions',
  },
  {
    key: 'queue',
    label: 'Queue depth',
    value: (s) => fmtInt(s.run_step_queue_depth),
    hint: 'run-step queue',
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
    <div className="bg-surface border-border-default shadow-pasture-sm rounded-lg border p-4">
      <p className="text-text-secondary text-xs font-semibold tracking-wider uppercase">{label}</p>
      <p className="font-display text-h2 text-text-primary mt-2 font-medium tabular-nums">{value}</p>
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
  return (
    <div className="grid grid-cols-2 gap-4 sm:grid-cols-3 lg:grid-cols-5">
      {STATS.map((stat) =>
        loading || !snapshot ? (
          <div
            key={stat.key}
            className="bg-surface border-border-default shadow-pasture-sm rounded-lg border p-4"
          >
            <div className="animate-pulse space-y-2">
              <div className="bg-surface-raised h-3 w-20 rounded" />
              <div className="bg-surface-raised h-7 w-16 rounded" />
            </div>
          </div>
        ) : (
          <StatCard key={stat.key} label={stat.label} value={stat.value(snapshot)} hint={stat.hint} />
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
  const rows: LoopRow[] = useMemo(() => {
    if (!loops) return [];
    return Object.entries(loops)
      .map(([name, l]) => ({ name, ...l }))
      .sort((a, b) => a.name.localeCompare(b.name));
  }, [loops]);

  return (
    <section className="bg-surface border-border-default shadow-pasture-sm rounded-lg border p-4">
      <h2 className="text-text-secondary mb-3 text-xs font-semibold tracking-wider uppercase">
        Scheduler loops
      </h2>
      {loading ? (
        <div className="animate-pulse space-y-2">
          {[1, 2, 3].map((i) => (
            <div key={i} className="bg-surface-raised h-6 rounded" />
          ))}
        </div>
      ) : rows.length === 0 ? (
        <p className="text-text-muted text-sm">No loop ticks recorded yet.</p>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full text-left font-mono text-xs">
            <thead>
              <tr className="text-text-muted border-border-default border-b">
                <th className="pr-3 pb-2 font-medium">Loop</th>
                <th className="pr-3 pb-2 text-right font-medium">Interval</th>
                <th className="pr-3 pb-2 text-right font-medium">Last duration</th>
                <th className="pb-2 text-right font-medium">Last tick</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((r) => (
                <tr key={r.name} className="border-border-default border-b last:border-0">
                  <td className="text-text-primary py-2 pr-3">{r.name}</td>
                  <td className="text-text-muted py-2 pr-3 text-right tabular-nums">
                    {r.interval_seconds}s
                  </td>
                  <td className="text-text-primary py-2 pr-3 text-right tabular-nums">
                    {fmtMs(r.last_duration_seconds)}
                  </td>
                  <td className="text-text-muted py-2 text-right tabular-nums">
                    {fmtRelTime(r.last_tick_iso)}
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
    <section className="bg-surface border-border-default shadow-pasture-sm rounded-lg border p-4">
      <h2 className="text-text-secondary mb-3 text-xs font-semibold tracking-wider uppercase">
        HTTP latency
      </h2>
      {loading ? (
        <div className="animate-pulse space-y-2">
          {[1, 2, 3, 4].map((i) => (
            <div key={i} className="bg-surface-raised h-6 rounded" />
          ))}
        </div>
      ) : rows.length === 0 ? (
        <p className="text-text-muted text-sm">No requests recorded yet.</p>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full text-left font-mono text-xs">
            <thead>
              <tr className="text-text-muted border-border-default border-b">
                <th className="pr-3 pb-2 font-medium">Route</th>
                <th className="pr-3 pb-2 text-right font-medium">Count</th>
                <th className="pr-3 pb-2 text-right font-medium">p50</th>
                <th className="pr-3 pb-2 text-right font-medium">p95</th>
                <th className="pb-2 text-right font-medium">Max</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((r) => (
                <tr
                  key={r.route}
                  className={cn(
                    'border-border-default border-b last:border-0',
                    r.isAggregate && 'bg-surface-raised/40',
                  )}
                >
                  <td className="max-w-64 py-2 pr-3">
                    <span
                      className={cn('block truncate', r.isAggregate ? 'text-text-primary font-medium' : 'text-text-primary')}
                      title={r.route}
                    >
                      {r.isAggregate ? 'All routes' : r.route}
                    </span>
                  </td>
                  <td className="text-text-muted py-2 pr-3 text-right tabular-nums">{fmtInt(r.count)}</td>
                  <td className="text-text-primary py-2 pr-3 text-right tabular-nums">{fmtMs(r.p50)}</td>
                  <td className="text-text-primary py-2 pr-3 text-right tabular-nums">{fmtMs(r.p95)}</td>
                  <td className="text-text-muted py-2 text-right tabular-nums">{fmtMs(r.max)}</td>
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
/*  History charts — Sparkline trends over persisted snapshots         */
/* ------------------------------------------------------------------ */

interface TrendDef {
  key: string;
  label: string;
  pick: (s: MetricsSnapshot) => number | null;
  fmt: (v: number) => string;
  variant: 'default' | 'green' | 'yellow' | 'red';
}

const TRENDS: TrendDef[] = [
  {
    key: 'queue',
    label: 'Queue depth',
    pick: (s) => s.run_step_queue_depth,
    fmt: (v) => fmtInt(v),
    variant: 'default',
  },
  {
    key: 'sessions',
    label: 'Active sessions',
    pick: (s) => s.executor_sessions_active,
    fmt: (v) => fmtInt(v),
    variant: 'green',
  },
  {
    key: 'tasks',
    label: 'Tasks in flight',
    pick: (s) => s.tasks?.pending_and_in_flight ?? null,
    fmt: (v) => fmtInt(v),
    variant: 'default',
  },
  {
    key: 'jobs',
    label: 'Jobs in flight',
    pick: (s) => s.jobs_in_flight,
    fmt: (v) => fmtInt(v),
    variant: 'yellow',
  },
  {
    key: 'p95',
    label: 'p95 latency · all routes',
    pick: (s) => s.http?.[AGGREGATE_KEY]?.p95 ?? null,
    fmt: (v) => fmtMs(v),
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
  const current = series.length > 0 ? series[series.length - 1]! : null;
  const peak = series.length > 0 ? Math.max(...series) : null;
  return (
    <div className="bg-surface border-border-default shadow-pasture-sm rounded-lg border p-4">
      <div className="flex items-baseline justify-between gap-2">
        <p className="text-text-secondary text-xs font-semibold tracking-wider uppercase">{def.label}</p>
        <p className="text-text-muted text-2xs tabular-nums">
          {peak != null ? `peak ${def.fmt(peak)}` : ''}
        </p>
      </div>
      <p className="font-display text-h3 text-text-primary mt-2 font-medium tabular-nums">
        {current != null ? def.fmt(current) : '—'}
      </p>
      <div className="mt-3">
        {series.length >= 2 ? (
          <Sparkline data={series} width={220} height={40} variant={def.variant} />
        ) : (
          <p className="text-text-muted text-2xs">Not enough history yet.</p>
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
  const seriesByKey = useMemo(() => {
    const map: Record<string, number[]> = {};
    for (const def of TRENDS) map[def.key] = trendSeries(rows ?? [], def.pick);
    return map;
  }, [rows]);

  const sampleCount = rows?.length ?? 0;

  return (
    <section>
      <div className="mb-3 flex items-center justify-between gap-2">
        <h2 className="text-text-secondary text-xs font-semibold tracking-wider uppercase">
          Trends · last {windowLabel}
        </h2>
        {!loading && !error && (
          <span className="text-text-muted text-2xs tabular-nums">
            {sampleCount} snapshot{sampleCount === 1 ? '' : 's'}
          </span>
        )}
      </div>
      {error ? (
        <div className="bg-surface border-border-default shadow-pasture-sm rounded-lg border p-6">
          <p className="text-feedback-danger text-sm">Couldn't load metrics history — try again.</p>
        </div>
      ) : loading ? (
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3">
          {[1, 2, 3].map((i) => (
            <div
              key={i}
              className="bg-surface border-border-default shadow-pasture-sm rounded-lg border p-4"
            >
              <div className="animate-pulse space-y-3">
                <div className="bg-surface-raised h-3 w-24 rounded" />
                <div className="bg-surface-raised h-7 w-16 rounded" />
                <div className="bg-surface-raised h-10 w-full rounded" />
              </div>
            </div>
          ))}
        </div>
      ) : sampleCount === 0 ? (
        <div className="bg-surface border-border-default shadow-pasture-sm rounded-lg border p-6">
          <p className="text-text-muted text-sm">
            No persisted snapshots in this window yet. History accrues as the daemon runs.
          </p>
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
/*  Window toggle (rounded-full pills — matches Spend/Agents)          */
/* ------------------------------------------------------------------ */

function WindowToggle({
  winIdx,
  onChange,
}: {
  winIdx: number;
  onChange: (i: number) => void;
}): JSX.Element {
  return (
    <div className="flex gap-1 font-mono text-xs" role="group" aria-label="History window">
      {WINDOWS.map((w, i) => (
        <button
          key={w.label}
          type="button"
          onClick={() => onChange(i)}
          aria-pressed={i === winIdx}
          className={cn(
            'rounded-full border border-transparent px-3 py-1 transition-colors',
            i === winIdx
              ? 'bg-accent-soft text-accent-text'
              : 'text-text-muted hover:text-text-primary',
          )}
        >
          {w.label}
        </button>
      ))}
    </div>
  );
}

/* ------------------------------------------------------------------ */
/*  Main page                                                          */
/* ------------------------------------------------------------------ */

export function HealthPage(): JSX.Element {
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
      <div className="p-6">
        <header className="mb-6 flex items-start justify-between gap-3">
          <PageHeader title="Runtime Health" meta="Live daemon metrics · all orgs" />
          <WindowToggle winIdx={winIdx} onChange={onChangeWindow} />
        </header>

        {liveQ.isError && (
          <div className="border-feedback-danger/30 bg-feedback-danger/5 mb-6 rounded-lg border p-4">
            <p className="text-feedback-danger text-sm">
              Couldn't load live metrics. Retrying automatically…
            </p>
          </div>
        )}

        {/* Summary cards */}
        <div className="mb-6">
          <SummaryCards snapshot={snapshot} loading={liveQ.isLoading} />
        </div>

        {/* Loop + HTTP tables */}
        <div className="mb-6 grid grid-cols-1 gap-4 lg:grid-cols-2">
          <LoopsCard loops={snapshot?.loops} loading={liveQ.isLoading} />
          <HttpCard http={snapshot?.http} loading={liveQ.isLoading} />
        </div>

        {/* History trends */}
        <HistorySection
          rows={historyQ.data}
          loading={historyQ.isLoading}
          error={historyQ.isError}
          windowLabel={win.label}
        />
      </div>
    </div>
  );
}
