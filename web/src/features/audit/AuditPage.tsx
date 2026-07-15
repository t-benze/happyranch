/**
 * AuditPage — unified day-grouped timeline surface (§4.12 PRD final).
 *
 * - Time window chips (24h / 7d / All time) control the query window.
 * - Right-rail "Event types" legend-filter (AUDIT-02): the ~57 raw audit
 *   event-types collapse into five human classes (Dispatch / Completed /
 *   Merge / Escalation / Failure), each with a colored dot + a per-class
 *   count. Clicking a class narrows the timeline to that class CLIENT-SIDE
 *   (counts + filtering both derive from the already-fetched rows — no extra
 *   round-trip, no /audit API change).
 * - Export button (placeholder until DERIVE route built).
 * - Timeline: day-grouped, most-recent first, with crescent-moon marker
 *   for dream-originated threads (A4).
 *
 * States: loading skeleton, empty ("No audit entries"), all-clear calm
 * ("All clear — no failures or escalations"), error with retry.
 */
import { useMemo, useCallback } from 'react';
import { useSearchParams } from 'react-router-dom';
import { Button } from '@/design-system/primitives/Button';
import { ContentWrap } from '@/design-system/layouts/ContentWrap/ContentWrap';
import { cn } from '@/lib/utils';
import { useAuditList } from '@/hooks/audit';
import { AuditTimeline } from './AuditTimeline';
import {
  decodeFilters,
  encodeFilters,
  buildClassLegend,
  classOf,
  sinceToISO,
  DOT_COLOR_CLASS,
  EVENT_CLASS_META,
  type AuditFilters,
  type ClassLegendEntry,
  type EventClass,
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

  // We need the FULL (unfiltered-by-class) set to build the legend counts.
  // The legend toggle drives a client-side filter, so the class counts stay
  // stable regardless of which class is selected.
  // Memoize the since ISO string so it's stable across renders — prevents
  // queryKey churn when sinceToISO produces a slightly different timestamp
  // on each render (different milliseconds from new Date()).
  const sinceISO = useMemo(() => sinceToISO(filters.since), [filters.since]);

  const fullQuery = useAuditList({
    agent: filters.agent,
    since: sinceISO,
    task_id: filters.task_id,
    limit: 500,
  });
  // Flatten every loaded page, then derive the legend/export from the whole
  // set — grouping and counts span pages, never resetting per page. Memoized
  // so the array identity is stable across renders, keeping the downstream
  // useMemo deps (legend / color map / filtered export) honest.
  const allEntries = useMemo(
    () => fullQuery.data?.pages.flatMap((p) => p.entries) ?? [],
    [fullQuery.data],
  );

  const legend = useMemo(() => buildClassLegend(allEntries), [allEntries]);

  // Per-action dot color for the timeline rows, derived from each action's
  // class color. Keyed by raw action so AuditTimeline's `legendMap.get(action)`
  // resolves a DotColor token.
  const legendColorMap = useMemo(() => {
    const m = new Map<string, string>();
    for (const e of allEntries) {
      if (!m.has(e.action)) m.set(e.action, EVENT_CLASS_META[classOf(e.action)].color);
    }
    return m;
  }, [allEntries]);

  // Set the window
  const setSince = useCallback(
    (since: AuditFilters['since']) => {
      setSearchParams(encodeFilters({ ...filters, since }), { replace: true });
    },
    [filters, setSearchParams],
  );

  // Toggle a class filter (clicking the active class clears it)
  const toggleClass = useCallback(
    (eventClass: EventClass) => {
      const next = filters.eventClass === eventClass ? null : eventClass;
      setSearchParams(encodeFilters({ ...filters, eventClass: next }), { replace: true });
    },
    [filters, setSearchParams],
  );

  const clearClass = useCallback(() => {
    setSearchParams(encodeFilters({ ...filters, eventClass: null }), { replace: true });
  }, [filters, setSearchParams]);

  // Client-side filtered entries used for the CSV export so it matches what the
  // timeline shows. The timeline server-filters by the raw `action` deep-link
  // AND narrows by the active `eventClass` client-side, so the export must apply
  // BOTH — otherwise a raw `?action=…` link (or a contradictory action+class
  // pair) makes the export dump rows the timeline never displayed.
  const filteredEntries = useMemo(() => {
    return allEntries.filter(
      (e) =>
        (!filters.action || e.action === filters.action) &&
        (!filters.eventClass || classOf(e.action) === filters.eventClass),
    );
  }, [allEntries, filters.action, filters.eventClass]);

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
      {/* EM ruling (THR-099 audit): KEEP the bordered card header + the side
          event-legend; cap the inner content at the shared 1180 `max-w-content`
          centered in the main region beside the shell rail, with the same 26px
          horizontal pad as tasks. Audit is a full-height, own-inner-scroll page
          (AuditTimeline scrolls internally + paginates against its own overflow
          box), so we REUSE <ContentWrap> purely for its 1180 cap + 26px pad and
          override its inner column to `flex h-full` — that keeps the bounded
          flex height AuditTimeline's internal scroll/infinite-load needs (its
          overflow-y-auto never engages because the inner column is exactly
          h-full). This avoids hand-rolling the arbitrary 26px pad, which eslint
          forbids in the feature layer. AuditTimeline itself is untouched. */}
      <ContentWrap className="flex h-full min-h-0 flex-col gap-4">
      {/* --- Top bar --- */}
      <header className="bg-surface border-border-default shrink-0 rounded-lg border p-4">
        <div className="flex items-start justify-between gap-3">
          {/* AUDIT-03: uppercase eyebrow + Newsreader serif title, matching the
              a-audit Direction-A reference and the Tasks/Agents surfaces. */}
          <div className="min-w-0 flex-1">
            <p className="text-text-muted text-xs font-medium tracking-wide uppercase">
              APPEND-ONLY · EVERY ACTION, WHO &amp; WHEN
            </p>
            <h1 className="font-display text-display text-text-primary mt-1 font-medium">
              The org&apos;s audit trail
            </h1>
          </div>
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
                    ? 'bg-accent-soft text-accent-text border border-transparent'
                    : 'bg-surface-sunken text-text-muted hover:bg-surface-raised hover:text-text-primary',
                )}
              >
                {opt.label}
              </button>
            );
          })}
        </div>
      </header>

      {/* Timeline (left) + Event-types legend-filter rail (right) */}
      <div className="flex min-h-0 flex-1 gap-4 overflow-hidden">
        {/* The timeline card MUST be a bounded-height flex COLUMN (`flex
            flex-col min-h-0`), not a plain block. AuditTimeline's TimelineBody
            is `flex-1 overflow-y-auto` and paginates via an IntersectionObserver
            whose root is that scroll box — TimelineBody only gets a height, and
            its inner scroll only engages, when its parent is a bounded flex
            column. A plain `overflow-hidden` block makes `flex-1` inert, so
            TimelineBody grows to full content height, the card clips it with no
            scrollbar, the bottom sentinel sits below the clip and never
            re-intersects, and infinite scroll dies ("can't load more"). */}
        <div className="border-border-default bg-surface flex min-h-0 min-w-0 flex-1 flex-col overflow-hidden rounded-lg border">
          <AuditTimeline legendMap={legendColorMap} sinceISO={sinceISO} />
        </div>
        <EventTypesRail
          legend={legend}
          activeClass={filters.eventClass}
          onToggle={toggleClass}
          onClear={clearClass}
        />
      </div>
      </ContentWrap>
    </div>
  );
}

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

/** Right-rail legend-filter: the five fixed event classes with colored dots +
 *  per-class counts. Each row is a toggle (aria-pressed) that filters the
 *  timeline to that class; clicking the active class — or "Show all events" —
 *  clears the filter. */
function EventTypesRail({
  legend,
  activeClass,
  onToggle,
  onClear,
}: {
  legend: ClassLegendEntry[];
  activeClass: EventClass | null;
  onToggle: (eventClass: EventClass) => void;
  onClear: () => void;
}): JSX.Element {
  return (
    <aside
      className="bg-surface border-border-default h-fit w-56 shrink-0 rounded-lg border p-4"
      aria-label="Event type filter"
    >
      <h2 className="text-text-secondary font-display mb-2 text-sm font-medium">Event types</h2>
      <ul className="space-y-0.5">
        {legend.map((le) => {
          const active = activeClass === le.eventClass;
          return (
            <li key={le.eventClass}>
              <button
                type="button"
                aria-pressed={active}
                onClick={() => onToggle(le.eventClass)}
                className={cn(
                  'flex w-full items-center gap-2 rounded-md px-2 py-1.5 text-sm transition-colors',
                  active
                    ? 'bg-accent-soft text-accent-text'
                    : 'text-text-primary hover:bg-surface-raised',
                )}
              >
                <span
                  aria-hidden="true"
                  className={cn('inline-block h-2 w-2 shrink-0 rounded-full', DOT_COLOR_CLASS[le.color])}
                />
                <span className="flex-1 text-left">{le.label}</span>
                <span className="text-text-muted tabular-nums">{le.count}</span>
              </button>
            </li>
          );
        })}
      </ul>
      {activeClass && (
        <button
          type="button"
          onClick={onClear}
          className="text-accent-text hover:text-text-primary mt-3 text-xs"
        >
          Show all events
        </button>
      )}
    </aside>
  );
}
