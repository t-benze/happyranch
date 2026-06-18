/**
 * AuditPage — unified day-grouped timeline surface (§4.12 PRD final).
 *
 * - Time window chips (24h / 7d / All time) control the query window.
 * - Event-type legend with counts doubles as filter: clicking a legend
 *   entry filters the timeline to that event type.
 * - Export button (placeholder until DERIVE route built).
 * - Timeline: day-grouped, most-recent first, with crescent-moon marker
 *   for dream-originated threads (A4).
 *
 * States: loading skeleton, empty ("No audit entries"), all-clear calm
 * ("All clear — no failures or escalations"), error with retry.
 */
import { useMemo, useCallback } from 'react';
import { useSearchParams } from 'react-router-dom';
import { PageHeader } from '@/design-system/patterns/PageHeader';
import { Button } from '@/design-system/primitives/Button';
import { cn } from '@/lib/utils';
import { useAuditList } from '@/hooks/audit';
import { AuditTimeline } from './AuditTimeline';
import {
  decodeFilters,
  encodeFilters,
  buildLegend,
  sinceToISO,
  type AuditFilters,
  type LegendEntry,
} from './audit-filters';
import type { AuditEntry } from '@/lib/api/types';

const SINCE_OPTIONS: { value: AuditFilters['since']; label: string }[] = [
  { value: '24h', label: '24h' },
  { value: '7d', label: '7d' },
  { value: 'all', label: 'All time' },
];

export function AuditPage(): JSX.Element {
  const [searchParams, setSearchParams] = useSearchParams();
  const filters = useMemo(() => decodeFilters(searchParams), [searchParams]);

  // We need the FULL (unfiltered-by-action) set to build the legend counts.
  // The timeline query is also unfiltered by action; the legend toggle
  // drives a client-side filter. This avoids a second round-trip per legend
  // click and makes the count totals stable.
  const fullQuery = useAuditList({
    agent: filters.agent,
    since: sinceToISO(filters.since),
    task_id: filters.task_id,
    limit: 500,
  });
  const allEntries = fullQuery.data?.entries ?? [];

  const legend = useMemo(() => buildLegend(allEntries), [allEntries]);

  // Build color map for legend dots → TimelineRow
  const legendColorMap = useMemo(() => {
    const m = new Map<string, string>();
    for (const le of legend) m.set(le.action, le.color);
    return m;
  }, [legend]);

  // Set the window
  const setSince = useCallback(
    (since: AuditFilters['since']) => {
      setSearchParams(encodeFilters({ ...filters, since, action: null }), { replace: true });
    },
    [filters, setSearchParams],
  );

  // Toggle a legend filter
  const toggleAction = useCallback(
    (action: string) => {
      const next = filters.action === action ? null : action;
      setSearchParams(encodeFilters({ ...filters, action: next }), { replace: true });
    },
    [filters, setSearchParams],
  );

  const clearAction = useCallback(() => {
    setSearchParams(encodeFilters({ ...filters, action: null }), { replace: true });
  }, [filters, setSearchParams]);

  // Client-side filtered entries (legend + time-window, already in memory)
  const filteredEntries = useMemo(() => {
    if (!filters.action) return allEntries;
    return allEntries.filter((e) => e.action === filters.action);
  }, [allEntries, filters.action]);

  // Export the currently-visible (filtered) audit entries as CSV
  const handleExport = useCallback(() => {
    if (filteredEntries.length === 0) return;
    const csv = auditEntriesToCSV(filteredEntries);
    const blob = new Blob([csv], { type: 'text/csv;charset=utf-8;' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `audit-${new Date().toISOString().slice(0, 10)}.csv`;
    a.click();
    // jsdom may not implement revokeObjectURL
    try { URL.revokeObjectURL(url); } catch { /* noop */ }
  }, [filteredEntries]);

  return (
    <div className="bg-surface-canvas flex h-full flex-col">
      {/* --- Top bar --- */}
      <header className="border-border-subtle border-b p-4">
        <div className="flex items-start justify-between gap-3">
          <PageHeader
            title="Audit"
            meta="Immutable, append-only forensic record — what happened, who, when."
          />
          <Button variant="secondary" size="sm" onClick={handleExport}>
            Export
          </Button>
        </div>

        {/* Time window chips */}
        <div className="mt-3 flex items-center gap-2" role="radiogroup" aria-label="Time window">
          {SINCE_OPTIONS.map((opt) => {
            const active = (filters.since ?? 'all') === (opt.value ?? 'all');
            return (
              <button
                key={opt.label}
                role="radio"
                aria-checked={active}
                type="button"
                onClick={() => setSince(opt.value)}
                className={cn(
                  'rounded-full px-3 py-1 text-xs font-medium transition-colors',
                  active
                    ? 'bg-accent-default text-accent-contrast'
                    : 'bg-surface-sunken text-fg-muted hover:bg-surface-raised hover:text-fg',
                )}
              >
                {opt.label}
              </button>
            );
          })}
        </div>

        {/* Legend-as-filter */}
        {legend.length > 0 && (
          <div className="mt-3 flex flex-wrap items-center gap-2" role="group" aria-label="Event type filter">
            <LegendFilter
              legend={legend}
              activeAction={filters.action}
              onToggle={toggleAction}
              onClear={clearAction}
            />
          </div>
        )}
      </header>

      {/* Active filter banner */}
      {filters.action && (
        <div className="bg-surface-sunken border-border-subtle flex items-center gap-2 border-b px-4 py-1.5 text-xs">
          <span className="text-fg-muted">Filtered:</span>
          <span className="text-fg font-medium">{filters.action}</span>
          <button
            type="button"
            onClick={clearAction}
            className="text-accent hover:underline"
          >
            Clear filter
          </button>
        </div>
      )}

      {/* Timeline */}
      <div className="flex-1 overflow-hidden">
        <AuditTimeline legendMap={legendColorMap} />
      </div>
    </div>
  );
}

/* ------------------------------------------------------------------ */
/*  Legend filter row                                                  */
/* ------------------------------------------------------------------ */

const DOT_COLOR: Record<string, string> = {
  green: 'bg-feedback-success',
  amber: 'bg-feedback-warning',
  red: 'bg-tier-red',
};

/** Convert audit entries to CSV string. Respects the currently active
 *  legend filter + time-window (caller provides filtered entries). */
export function auditEntriesToCSV(entries: AuditEntry[]): string {
  const headers = ['timestamp', 'task_id', 'agent', 'action', 'executor', 'tokens', 'dream_id', 'job_id'];
  const rows = entries.map((e) => {
    const executor = e.payload.executor as string | undefined ?? '';
    const tokens = (() => {
      const tu = e.payload.token_usage;
      if (tu && typeof tu === 'object' && 'total' in tu) {
        const t = (tu as Record<string, unknown>).total;
        if (typeof t === 'number') return String(t);
      }
      const tc = e.payload.token_count;
      return tc != null ? String(tc) : '';
    })();
    const dreamId = e._thread_dream_id ?? '';
    const jobId = e.payload.script_request_id as string | undefined ?? '';
    return [
      e.timestamp,
      e.task_id ?? '',
      e.agent ?? '',
      e.action,
      executor,
      tokens,
      dreamId,
      jobId,
    ].map(escapeCSV).join(',');
  });
  return [headers.join(','), ...rows].join('\n');
}

function escapeCSV(field: string): string {
  if (field.includes(',') || field.includes('"') || field.includes('\n')) {
    return `"${field.replace(/"/g, '""')}"`;
  }
  return field;
}

function LegendFilter({
  legend,
  activeAction,
  onToggle,
  onClear,
}: {
  legend: LegendEntry[];
  activeAction: string | null;
  onToggle: (action: string) => void;
  onClear: () => void;
}): JSX.Element {
  return (
    <>
      {activeAction && (
        <button
          type="button"
          onClick={onClear}
          className="text-fg-muted hover:text-fg rounded-full px-2 py-0.5 text-xs"
        >
          All
        </button>
      )}
      {legend.map((le) => {
        const active = activeAction === le.action;
        return (
          <button
            key={le.action}
            type="button"
            onClick={() => onToggle(le.action)}
            className={cn(
              'inline-flex items-center gap-1.5 rounded-full px-2.5 py-1 text-xs transition-colors',
              active
                ? 'bg-accent-muted ring-accent-default ring-1'
                : 'bg-surface-sunken hover:bg-surface-raised',
            )}
          >
            <span
              aria-hidden="true"
              className={cn('inline-block h-2 w-2 rounded-full', DOT_COLOR[le.color] ?? 'bg-fg-muted')}
            />
            <span className="text-fg">{le.label}</span>
            <span className="text-fg-muted">{le.count}</span>
          </button>
        );
      })}
    </>
  );
}
