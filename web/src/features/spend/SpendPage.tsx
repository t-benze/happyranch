/**
 * SpendPage — Direction-A Pasture fidelity pass (THR-030 Leg B batch 5).
 *
 * Tokens-only observability. No dollar amounts — render "$0.00 · not metered"
 * per the honesty fence. Cache reads in a separate column, never folded into
 * churn. Churn = input + output + reasoning.
 *
 * Window toggle (24h/7d/30d) — rounded-full pill buttons with
 * accent-soft/accent-text active state (matching the shipped Agents executor
 * segmented control). Drives the ACTUAL query window; headings read from the
 * selected window label (not a hard-coded string). Storage in localStorage.
 *
 * Breakdown: agent / thread / model (segmented control, same pill vocabulary).
 * Hero burn numeral: font-display serif (Newsreader). Cards: bg-surface +
 * rounded-lg + shadow-pasture-sm.
 *
 * Export: client-side CSV of currently visible breakdown data.
 *
 * States: Loading (Pasture skeletons), Empty (calm display-font empty state),
 * Error (retry), Populated (hero + breakdown + top-threads).
 */
import { useMemo, useState, useCallback, useRef } from 'react';
import { useSpendByAgent, useSpendByThread, useSpendByModel } from '@/hooks/spend';
import { cn } from '@/lib/utils';
// classifyModel is the single canonical model-label renderer; Spend and
// Dashboard must never disagree. Moving it out of @/features/dashboard
// would be a cosmetic refactor that adds no safety value.
// eslint-disable-next-line no-restricted-imports
import { classifyModel } from '@/features/dashboard/topTokens';
import { Button } from '@/design-system/primitives/Button';
import { PageHeader } from '@/design-system/patterns/PageHeader';
import type { TokenUsageRollup } from '@/hooks/spend';

/* ------------------------------------------------------------------ */
/*  Window constants                                                   */
/* ------------------------------------------------------------------ */

const WINDOWS = [
  { label: '24h', ms: 24 * 60 * 60 * 1000 },
  { label: '7d', ms: 7 * 24 * 60 * 60 * 1000 },
  { label: '30d', ms: 30 * 24 * 60 * 60 * 1000 },
] as const;

type BreakdownSegment = 'agent' | 'thread' | 'model';

/* ------------------------------------------------------------------ */
/*  Storage helpers                                                    */
/* ------------------------------------------------------------------ */

const STORAGE_KEY = 'hr-spend-window';

function loadWindowIdx(): number {
  try {
    const v = localStorage.getItem(STORAGE_KEY);
    if (v != null) {
      const n = parseInt(v, 10);
      if (n >= 0 && n < WINDOWS.length) return n;
    }
  } catch { /* storage unavailable */ }
  return 1; // default 7d
}

function saveWindowIdx(idx: number): void {
  try {
    localStorage.setItem(STORAGE_KEY, String(idx));
  } catch { /* storage unavailable */ }
}

/* ------------------------------------------------------------------ */
/*  Format helpers                                                     */
/* ------------------------------------------------------------------ */

function fmtNum(n: number): string {
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`;
  if (n >= 1_000) return `${(n / 1_000).toFixed(1)}K`;
  return n.toLocaleString();
}

function pct(part: number, whole: number): string {
  if (whole === 0) return '0%';
  return `${Math.round((part / whole) * 100)}%`;
}

/* ------------------------------------------------------------------ */
/*  Hero section — Pasture card w/ font-display burn numeral            */
/* ------------------------------------------------------------------ */

function HeroCard({
  totalChurn,
  cacheRead,
  inputTokens,
  outputTokens,
  windowLabel,
  loading,
}: {
  totalChurn: number;
  cacheRead: number;
  inputTokens: number;
  outputTokens: number;
  windowLabel: string;
  loading: boolean;
}): JSX.Element {
  if (loading) {
    return (
      <section className="bg-surface border-border-default shadow-pasture-sm rounded-lg border p-6">
        <div className="animate-pulse space-y-3">
          <div className="bg-surface-raised h-3 w-28 rounded" />
          <div className="bg-surface-raised h-10 w-36 rounded" />
          <div className="bg-surface-raised h-4 w-44 rounded" />
        </div>
      </section>
    );
  }

  if (totalChurn === 0) {
    return (
      <section className="bg-surface border-border-default shadow-pasture-sm rounded-lg border p-6">
        <p className="text-text-secondary text-xs font-semibold tracking-wider uppercase">
          Token burn · {windowLabel}
        </p>
        <p className="font-display text-display text-text-primary mt-2 font-medium tabular-nums">
          0
        </p>
        {/* Dollars always zero — honesty fence: no dollar metric in data-model. */}
        <p className="text-text-muted mt-1 text-sm">$0.00 · not metered</p>
        <p className="text-text-muted mt-2 text-sm">No token spend in this window</p>
      </section>
    );
  }

  return (
    <section className="bg-surface border-border-default shadow-pasture-sm rounded-lg border p-6">
      <p className="text-text-secondary text-xs font-semibold tracking-wider uppercase">
        Token burn · {windowLabel}
      </p>
      <p className="font-display text-display text-text-primary mt-2 font-medium tabular-nums">
        {fmtNum(totalChurn)}
      </p>
      {/* Dollars always zero — honesty fence: no dollar metric in data-model. */}
      <p className="text-text-muted mt-1 text-sm">$0.00 · not metered</p>
      <div className="border-border-default mt-4 grid grid-cols-3 gap-4 border-t pt-4">
        <div>
          <p className="text-text-muted text-xs">Fresh</p>
          <p className="font-display text-h2 text-text-primary font-medium tabular-nums">
            {fmtNum(totalChurn)}
          </p>
          <p className="text-text-muted text-2xs">input + output + reasoning</p>
        </div>
        <div>
          <p className="text-text-muted text-xs">From cache</p>
          <p className="font-display text-h2 text-text-primary font-medium tabular-nums">
            {fmtNum(cacheRead)}
          </p>
          <p className="text-text-muted text-2xs">
            {cacheRead > 0 ? pct(cacheRead, totalChurn + cacheRead) + ' of all reads' : 'none'}
          </p>
        </div>
        <div>
          <p className="text-text-muted text-xs">Detail</p>
          <p className="font-mono text-body text-text-primary tabular-nums">
            in {fmtNum(inputTokens)} / out {fmtNum(outputTokens)}
          </p>
        </div>
      </div>
    </section>
  );
}

/* ------------------------------------------------------------------ */
/*  Breakdown table                                                    */
/* ------------------------------------------------------------------ */

interface BreakdownRow {
  key: string;
  label: string;
  sessions: number;
  inputTokens: number;
  outputTokens: number;
  cacheReadTokens: number;
  totalTokens: number;
}

const SEGMENTS: { key: BreakdownSegment; label: string }[] = [
  { key: 'agent', label: 'Agent' },
  { key: 'thread', label: 'Thread' },
  { key: 'model', label: 'Model' },
];

const BAR_W = 128;
const BAR_H = 10;

function buildModelLabel(row: TokenUsageRollup): string {
  // The by-model rollup has a raw `model` field (string|null).
  // If NULL, render honestly — never a guessed correction.
  if (row.model == null) return '(unknown)';
  return row.model;
}

function BreakdownTable({
  segment,
  agentRollup,
  threadRollup,
  modelRollup,
  loading,
  error,
}: {
  segment: BreakdownSegment;
  agentRollup: TokenUsageRollup[];
  threadRollup: TokenUsageRollup[];
  modelRollup: TokenUsageRollup[];
  loading: boolean;
  error: boolean;
}): JSX.Element {
  const rows: BreakdownRow[] = useMemo(() => {
    let raw: TokenUsageRollup[];
    switch (segment) {
      case 'agent':
        raw = agentRollup;
        return raw
          .map((r): BreakdownRow => ({
            key: r.agent!,
            label: r.agent!,
            sessions: r.sessions,
            inputTokens: r.input_tokens,
            outputTokens: r.output_tokens,
            cacheReadTokens: r.cache_read_tokens,
            totalTokens: r.total_tokens,
          }))
          .sort((a, b) => b.totalTokens - a.totalTokens);
      case 'thread':
        raw = threadRollup;
        return raw
          .map((r): BreakdownRow => ({
            key: r.thread_id!,
            label: r.thread_id!,
            sessions: r.sessions,
            inputTokens: r.input_tokens,
            outputTokens: r.output_tokens,
            cacheReadTokens: r.cache_read_tokens,
            totalTokens: r.total_tokens,
          }))
          .sort((a, b) => b.totalTokens - a.totalTokens);
      case 'model':
        raw = modelRollup;
        return raw
          .map((r): BreakdownRow => ({
            key: r.model ?? '__null__',
            label: buildModelLabel(r),
            sessions: r.sessions,
            inputTokens: r.input_tokens,
            outputTokens: r.output_tokens,
            cacheReadTokens: r.cache_read_tokens,
            totalTokens: r.total_tokens,
          }))
          .sort((a, b) => b.totalTokens - a.totalTokens);
    }
  }, [segment, agentRollup, threadRollup, modelRollup]);

  if (error) {
    return (
      <div className="bg-surface border-border-default shadow-pasture-sm rounded-lg border p-6">
        <p className="text-feedback-danger text-sm">Couldn't load spend breakdown — retry</p>
      </div>
    );
  }

  if (loading) {
    return (
      <div className="bg-surface border-border-default shadow-pasture-sm rounded-lg border p-6">
        <div className="animate-pulse space-y-2">
          {[1, 2, 3, 4].map((i) => (
            <div key={i} className="bg-surface-raised h-6 rounded" />
          ))}
        </div>
      </div>
    );
  }

  if (rows.length === 0) {
    return (
      <div className="bg-surface border-border-default shadow-pasture-sm rounded-lg border p-6">
        <p className="text-text-muted text-sm">No token spend in this window</p>
      </div>
    );
  }

  const maxTokens = Math.max(...rows.map((r) => r.totalTokens), 1);

  return (
    <div className="bg-surface border-border-default shadow-pasture-sm rounded-lg border p-4">
      <div className="overflow-x-auto">
        <table className="w-full text-left font-mono text-xs">
          <thead>
            <tr className="text-text-muted border-border-default border-b">
              <th className="pr-3 pb-2 font-medium">{segment === 'agent' ? 'Agent' : segment === 'thread' ? 'Thread' : 'Model'}</th>
              <th className="pr-3 pb-2 text-right font-medium">Sessions</th>
              <th className="pr-3 pb-2 font-medium">Burn</th>
              <th className="pr-3 pb-2 text-right font-medium">Total</th>
              <th className="pb-2 text-right font-medium">Cache reads</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((r) => (
              <tr key={r.key} className="border-border-default border-b last:border-0">
                <td className="text-text-primary max-w-48 truncate py-2 pr-3" title={r.label}>
                  {r.label}
                </td>
                <td className="text-text-muted py-2 pr-3 text-right tabular-nums">{r.sessions}</td>
                <td className="py-2 pr-3">
                  <svg width={BAR_W} height={BAR_H} aria-hidden="true">
                    <rect
                      x={0}
                      y={0}
                      width={Math.max((r.totalTokens / maxTokens) * BAR_W, 1)}
                      height={BAR_H}
                      rx={1}
                      className="fill-accent"
                    />
                  </svg>
                </td>
                <td className="text-text-primary py-2 pr-3 text-right tabular-nums">{r.totalTokens.toLocaleString()}</td>
                <td className="text-text-muted py-2 text-right tabular-nums">
                  {r.cacheReadTokens.toLocaleString()}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

/* ------------------------------------------------------------------ */
/*  Top threads table (derived from thread rollup)                     */
/* ------------------------------------------------------------------ */

interface TopThreadRow {
  threadId: string;
  modelLabel: string;
  sessions: number;
  totalTokens: number;
  cacheReadTokens: number;
}

/** Rank threads by totalTokens DESC, slice to top N. Uses the shared
 *  classifyModel helper from @/features/dashboard/topTokens so Spend and the
 *  dashboard never disagree about a row's model label. */
function rankTopThreads(rollup: TokenUsageRollup[], topN: number): TopThreadRow[] {
  return rollup
    .map((r): TopThreadRow => ({
      threadId: r.thread_id ?? '(no thread)',
      modelLabel: classifyModel(r),
      sessions: r.sessions,
      totalTokens: r.total_tokens,
      cacheReadTokens: r.cache_read_tokens,
    }))
    .sort(
      (a, b) =>
        b.totalTokens - a.totalTokens ||
        b.sessions - a.sessions ||
        a.threadId.localeCompare(b.threadId),
    )
    .slice(0, topN);
}

function TopThreadsTable({
  threadRollup,
  loading,
  error,
}: {
  threadRollup: TokenUsageRollup[];
  loading: boolean;
  error: boolean;
}): JSX.Element {
  const rows = useMemo(() => rankTopThreads(threadRollup, 10), [threadRollup]);
  const maxTokens = Math.max(...rows.map((r) => r.totalTokens), 1);

  if (error) {
    return (
      <div className="bg-surface border-border-default shadow-pasture-sm rounded-lg border p-4">
        <p className="text-feedback-danger text-sm">Failed to load top threads.</p>
      </div>
    );
  }

  if (loading) {
    return (
      <div className="bg-surface border-border-default shadow-pasture-sm rounded-lg border p-4">
        <div className="animate-pulse space-y-2">
          {[1, 2, 3].map((i) => (
            <div key={i} className="bg-surface-raised h-5 rounded" />
          ))}
        </div>
      </div>
    );
  }

  if (rows.length === 0) {
    return (
      <div className="bg-surface border-border-default shadow-pasture-sm rounded-lg border p-4">
        <p className="text-text-muted text-sm">No token usage in window.</p>
      </div>
    );
  }

  return (
    <div className="bg-surface border-border-default shadow-pasture-sm rounded-lg border p-4">
      <h2 className="text-text-secondary text-xs font-semibold tracking-wider uppercase mb-3">
        Top threads by burn
      </h2>
      <ul className="space-y-1.5 font-mono text-xs">
        {rows.map((r) => {
          return (
            <li key={r.threadId} className="flex items-center gap-2">
              <span className="text-text-primary w-24 shrink-0 truncate" title={r.threadId}>
                {r.threadId}
              </span>
              <span className="text-text-muted w-28 shrink-0 truncate" title={r.modelLabel}>
                {r.modelLabel}
              </span>
              <svg width={BAR_W} height={BAR_H} className="shrink-0" aria-hidden="true">
                <rect
                  x={0}
                  y={0}
                  width={Math.max((r.totalTokens / maxTokens) * BAR_W, 1)}
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
                title="cache reads — never counted toward burn"
              >
                {r.cacheReadTokens.toLocaleString()}
                <span className="text-text-disabled ml-1.5">cache</span>
              </span>
            </li>
          );
        })}
      </ul>
    </div>
  );
}

/* ------------------------------------------------------------------ */
/*  Keyboard-aware segmented controls (ArrowLeft/ArrowRight roving)    */
/* ------------------------------------------------------------------ */

function useRovingFocus<TElement extends HTMLElement>(itemCount: number) {
  const refs = useRef<(TElement | null)[]>([]);
  const handleKeyDown = useCallback(
    (idx: number) => (e: React.KeyboardEvent<TElement>) => {
      if (e.key === 'ArrowRight') {
        e.preventDefault();
        const next = (idx + 1) % itemCount;
        refs.current[next]?.focus();
      } else if (e.key === 'ArrowLeft') {
        e.preventDefault();
        const prev = (idx - 1 + itemCount) % itemCount;
        refs.current[prev]?.focus();
      }
    },
    [itemCount],
  );
  return { refs, handleKeyDown };
}

function WindowToggle({
  winIdx,
  onChangeWindow,
}: {
  winIdx: number;
  onChangeWindow: (i: number) => void;
}): JSX.Element {
  const { refs, handleKeyDown } = useRovingFocus<HTMLButtonElement>(WINDOWS.length);
  return (
    <div className="flex gap-1 font-mono text-xs" role="group" aria-label="Spend window">
      {WINDOWS.map((w, i) => (
        <button
          key={w.label}
          type="button"
          ref={(el) => { refs.current[i] = el; }}
          onClick={() => onChangeWindow(i)}
          onKeyDown={handleKeyDown(i)}
          aria-pressed={i === winIdx}
          className={cn(
            'rounded-full px-3 py-1 transition-colors',
            i === winIdx
              ? 'bg-accent-soft text-accent-text border border-transparent'
              : 'text-text-muted hover:text-text-primary border border-transparent',
          )}
        >
          {w.label}
        </button>
      ))}
    </div>
  );
}

function BreakdownToggle({
  segment,
  setSegment,
}: {
  segment: BreakdownSegment;
  setSegment: (s: BreakdownSegment) => void;
}): JSX.Element {
  const { refs, handleKeyDown } = useRovingFocus<HTMLButtonElement>(SEGMENTS.length);
  return (
    <div className="flex gap-1 font-mono text-xs" role="group" aria-label="Breakdown by">
      {SEGMENTS.map((s, i) => (
        <button
          key={s.key}
          type="button"
          ref={(el) => { refs.current[i] = el; }}
          onClick={() => setSegment(s.key)}
          onKeyDown={handleKeyDown(i)}
          aria-pressed={segment === s.key}
          className={cn(
            'rounded-full px-3 py-1 transition-colors',
            segment === s.key
              ? 'bg-accent-soft text-accent-text border border-transparent'
              : 'text-text-muted hover:text-text-primary border border-transparent',
          )}
        >
          {s.label}
        </button>
      ))}
    </div>
  );
}

/* ------------------------------------------------------------------ */
/*  CSV export helper                                                  */
/* ------------------------------------------------------------------ */

function breakdownRowsToCSV(rows: BreakdownRow[]): string {
  const header = 'Label,Sessions,Fresh (total),Cache reads';
  const body = rows
    .map(
      (r) => `"${r.label}",${r.sessions},${r.totalTokens},${r.cacheReadTokens}`,
    )
    .join('\n');
  return `${header}\n${body}\n`;
}

function downloadCSV(csv: string, filename: string): void {
  const blob = new Blob([csv], { type: 'text/csv;charset=utf-8;' });
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = filename;
  a.click();
  // jsdom may not provide revokeObjectURL; guard defensively.
  if (typeof URL.revokeObjectURL === 'function') {
    URL.revokeObjectURL(url);
  }
}

/* ------------------------------------------------------------------ */
/*  Main page                                                          */
/* ------------------------------------------------------------------ */

export function SpendPage(): JSX.Element {
  const [winIdx, setWinIdx] = useState<number>(loadWindowIdx);
  const win = WINDOWS[winIdx];
  const since = useMemo(
    () => new Date(Date.now() - win.ms).toISOString(),
    [win.ms],
  );

  const agentQ = useSpendByAgent({ since });
  const threadQ = useSpendByThread({ since });
  const modelQ = useSpendByModel({ since });

  const [segment, setSegment] = useState<BreakdownSegment>('agent');

  const onChangeWindow = useCallback((i: number) => {
    setWinIdx(i);
    saveWindowIdx(i);
  }, []);

  // Hero totals: sum the agent rollup (or fallback to thread rollup)
  const heroTotals = useMemo(() => {
    const rows = agentQ.data ?? threadQ.data ?? [];
    let totalChurn = 0;
    let cacheRead = 0;
    let inputTokens = 0;
    let outputTokens = 0;
    for (const r of rows) {
      totalChurn += r.total_tokens;
      cacheRead += r.cache_read_tokens;
      inputTokens += r.input_tokens;
      outputTokens += r.output_tokens;
    }
    return { totalChurn, cacheRead, inputTokens, outputTokens };
  }, [agentQ.data, threadQ.data]);

  // Build exportable breakdown rows (mirrors BreakdownTable logic)
  const exportRows = useMemo((): BreakdownRow[] => {
    let raw: TokenUsageRollup[];
    switch (segment) {
      case 'agent':
        raw = agentQ.data ?? [];
        break;
      case 'thread':
        raw = threadQ.data ?? [];
        break;
      case 'model':
        raw = modelQ.data ?? [];
        break;
      default:
        raw = [];
    }
    return raw
      .map((r): BreakdownRow => ({
        key: segment === 'agent' ? r.agent! : segment === 'thread' ? r.thread_id! : r.model ?? '__null__',
        label: segment === 'agent' ? r.agent! : segment === 'thread' ? r.thread_id! : buildModelLabel(r),
        sessions: r.sessions,
        inputTokens: r.input_tokens,
        outputTokens: r.output_tokens,
        cacheReadTokens: r.cache_read_tokens,
        totalTokens: r.total_tokens,
      }))
      .sort((a, b) => b.totalTokens - a.totalTokens);
  }, [segment, agentQ.data, threadQ.data, modelQ.data]);

  const handleExport = useCallback(() => {
    const csv = breakdownRowsToCSV(exportRows);
    downloadCSV(csv, `spend-${segment}-${win.label}.csv`);
  }, [exportRows, segment, win.label]);

  const isAnyLoading = agentQ.isLoading || threadQ.isLoading || modelQ.isLoading;
  const isAnyError = agentQ.isError || threadQ.isError || modelQ.isError;

  return (
    <div className="bg-surface-canvas h-full overflow-y-auto">
      <div className="mx-auto max-w-5xl p-6">
        {/* Header — Pasture PageHeader pattern */}
        <header className="mb-6 flex items-start justify-between gap-3">
          <PageHeader
            title="Spend"
            meta="Token usage and cache savings"
          />
          <div className="flex items-center gap-3">
            <WindowToggle winIdx={winIdx} onChangeWindow={onChangeWindow} />
            {exportRows.length > 0 && !isAnyLoading && (
              <Button variant="secondary" size="sm" onClick={handleExport}>
                Export
              </Button>
            )}
          </div>
        </header>

        {/* Error banner */}
        {isAnyError && !isAnyLoading && (
          <div className="border-feedback-danger/30 bg-feedback-danger/5 mb-6 rounded-lg border p-4">
            <p className="text-feedback-danger text-sm">
              Couldn't load spend data. Try changing the window to reload.
            </p>
          </div>
        )}

        {/* Hero */}
        <div className="mb-6">
          <HeroCard
            totalChurn={heroTotals.totalChurn}
            cacheRead={heroTotals.cacheRead}
            inputTokens={heroTotals.inputTokens}
            outputTokens={heroTotals.outputTokens}
            windowLabel={win.label}
            loading={isAnyLoading && !heroTotals.totalChurn}
          />
        </div>

        {/* Breakdown */}
        <div className="mb-6">
          <div className="mb-3 flex items-center justify-between">
            <h2 className="text-text-secondary text-xs font-semibold tracking-wider uppercase">
              Where it went
            </h2>
            <BreakdownToggle segment={segment} setSegment={setSegment} />
          </div>
          <BreakdownTable
            segment={segment}
            agentRollup={agentQ.data ?? []}
            threadRollup={threadQ.data ?? []}
            modelRollup={modelQ.data ?? []}
            loading={isAnyLoading}
            error={isAnyError && !isAnyLoading}
          />
        </div>

        {/* Top threads */}
        <div className="mb-6">
          <TopThreadsTable
            threadRollup={threadQ.data ?? []}
            loading={threadQ.isLoading}
            error={!!threadQ.error}
          />
        </div>

      </div>
    </div>
  );
}
