/**
 * AuditTimeline — day-grouped reverse-chronological timeline.
 *
 * Each entry renders as a human-readable NARRATIVE SENTENCE with inline entity
 * links (agent / task / thread / job → existing client routes) + an event-class
 * colored dot + a mono secondary detail line + a right-aligned timestamp
 * (AUDIT-01, THR-030). The event → narrative transform lives in the pure,
 * unit-tested `describeAuditEntry`; this file only maps its segments to JSX.
 *
 * States: loading skeleton, empty ("No audit entries"), all-clear ("All clear
 * — no failures or escalations"), error with retry.
 */
import { useEffect, useMemo, useRef } from 'react';
import { Link, useParams, useSearchParams } from 'react-router-dom';
import { CrescentMoonBadge } from '@/design-system/patterns/CrescentMoonBadge';
import { EmptyState } from '@/design-system/patterns/EmptyState';
import { Button } from '@/design-system/primitives/Button';
import { cn } from '@/lib/utils';
import { useAuditList } from '@/hooks/audit';
import type { AuditEntry } from '@/lib/api/types';
import {
  decodeFilters,
  isAllClear,
  classOf,
  DOT_COLOR_CLASS,
} from './audit-filters';
import {
  describeAuditEntry,
  type EntityRef,
  type NarrativeSegment,
} from './audit-narrative';
import { useQueryClient } from '@tanstack/react-query';

/* ------------------------------------------------------------------ */
/*  Helpers                                                            */
/* ------------------------------------------------------------------ */

function formatTime(iso: string): string {
  // 24-hour mono clock (e.g. 14:10:02) matching the a-audit design authority —
  // never a 12-hour AM/PM meridiem (THR-099 Batch 2 fidelity).
  return new Date(iso).toLocaleTimeString(undefined, {
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
    hour12: false,
  });
}

const MONTH_ABBR = [
  'JAN', 'FEB', 'MAR', 'APR', 'MAY', 'JUN',
  'JUL', 'AUG', 'SEP', 'OCT', 'NOV', 'DEC',
] as const;

const DAY_MS = 24 * 60 * 60 * 1000;

/** Relative, uppercase date-group label matching the a-audit design authority:
 *  `TODAY · JUN 16`, `YESTERDAY · JUN 15`, else `WEEKDAY · MON DD` (THR-099
 *  PR2). `dateStr` is the UTC calendar day ("YYYY-MM-DD") produced by
 *  groupByDay's UTC slice, so today/yesterday are compared in UTC to stay
 *  consistent with the grouping. `now` is injectable for deterministic tests. */
export function formatDateHeader(dateStr: string, now: Date = new Date()): string {
  const todayStr = now.toISOString().slice(0, 10);
  const yesterdayStr = new Date(now.getTime() - DAY_MS).toISOString().slice(0, 10);
  const [, mm, dd] = dateStr.split('-');
  const monDay = `${MONTH_ABBR[Number(mm) - 1]} ${Number(dd)}`;

  let label: string;
  if (dateStr === todayStr) label = 'TODAY';
  else if (dateStr === yesterdayStr) label = 'YESTERDAY';
  else {
    label = new Date(`${dateStr}T00:00:00Z`)
      .toLocaleDateString('en-US', { weekday: 'long', timeZone: 'UTC' })
      .toUpperCase();
  }
  return `${label} · ${monDay}`;
}

/** Group entries by calendar day (date string), sort days most-recent first.
 *  Within each day, entries are sorted reverse-chronological (newest first)
 *  by timestamp, falling back to id DESC on tie. */
function groupByDay(entries: AuditEntry[]): { date: string; entries: AuditEntry[] }[] {
  const map = new Map<string, AuditEntry[]>();
  for (const e of entries) {
    const day = e.timestamp.slice(0, 10); // "YYYY-MM-DD"
    const bucket = map.get(day);
    if (bucket) bucket.push(e);
    else map.set(day, [e]);
  }
  return [...map.entries()]
    .sort((a, b) => (a[0] < b[0] ? 1 : -1))
    .map(([date, entries]) => ({
      date,
      entries: entries.sort((a, b) => {
        const tsA = a.timestamp;
        const tsB = b.timestamp;
        if (tsA > tsB) return -1;
        if (tsA < tsB) return 1;
        return b.id - a.id;
      }),
    }));
}

/* ------------------------------------------------------------------ */
/*  Entity links                                                       */
/* ------------------------------------------------------------------ */

/** Color per entity kind. task/thread reuse their locked id tokens; job and
 *  agent links use the accent token (no `--color-id-job` token exists). */
const REF_COLOR: Record<EntityRef['type'], string> = {
  task: 'text-id-task',
  thread: 'text-id-thread',
  job: 'text-accent-text',
  agent: 'text-accent-text',
};

function routeFor(ref: EntityRef, slug: string): string {
  switch (ref.type) {
    case 'task':
      return `/orgs/${slug}/tasks/${ref.id}`;
    case 'thread':
      return `/orgs/${slug}/threads/${ref.id}`;
    case 'job':
      return `/orgs/${slug}/jobs/${ref.id}`;
    case 'agent':
      return `/orgs/${slug}/agents/${ref.id}`;
  }
}

/** Render one narrative segment: bold subject, plain prose, or an entity link.
 *  Id-shaped refs (task/thread/job) render monospace; agent refs stay prose. */
function Segment({ seg, slug }: { seg: NarrativeSegment; slug: string }): JSX.Element {
  if (seg.kind === 'subject') {
    return <span className="text-text-primary font-medium">{seg.text}</span>;
  }
  if (seg.kind === 'text') {
    return <span>{seg.text}</span>;
  }
  const { ref } = seg;
  return (
    <Link
      to={routeFor(ref, slug)}
      className={cn(
        'hover:underline',
        REF_COLOR[ref.type],
        ref.type !== 'agent' && 'font-mono',
      )}
    >
      {ref.label}
    </Link>
  );
}

/* ------------------------------------------------------------------ */
/*  Entry row                                                          */
/* ------------------------------------------------------------------ */

interface TimelineRowProps {
  entry: AuditEntry;
  legendColor: string;
  slug: string;
}

function TimelineRow({ entry, legendColor, slug }: TimelineRowProps): JSX.Element {
  const narrative = describeAuditEntry(entry);
  const hasDream = !!entry._thread_dream_id;

  return (
    <div className="border-border-subtle hover:bg-surface-raised flex gap-3 border-b pr-3 pl-4 text-sm transition-colors">
      {/* Vertical dot-rail: a continuous 1px connector line threads every row of
          a day group; the color-coded status dot sits on the rail, painted over
          the line as an opaque node (a-audit dot-rail, THR-099 PR2). Vertical
          padding lives on the content column, not the row, so the rail spans the
          FULL row height and the connector stays unbroken across rows. */}
      <div aria-hidden="true" className="relative flex w-3 shrink-0 justify-center">
        {/* Full-height 1px connector line (border-l, centered) behind the dot.
            h-full spans the row (vertical padding lives on the content column),
            so consecutive rows' lines abut into one continuous rail. */}
        <span className="border-border-subtle absolute left-1/2 h-full border-l" />
        {/* Status dot on the rail, nudged to the first text line, painted over
            the line (relative → above the absolute connector). */}
        <span
          className={cn(
            'relative mt-3 h-2 w-2 rounded-full',
            DOT_COLOR_CLASS[legendColor as keyof typeof DOT_COLOR_CLASS] ?? 'bg-fg-muted',
          )}
        />
      </div>

      <div className="min-w-0 flex-1 py-2.5">
        {/* Narrative sentence + dream pill + timestamp */}
        <div className="flex items-baseline gap-3">
          <p className="text-text-secondary min-w-0 flex-1 leading-snug">
            {narrative.segments.map((seg, i) => (
              <Segment key={i} seg={seg} slug={slug} />
            ))}
          </p>
          {hasDream && (
            <span className="bg-accent-soft text-accent-text inline-flex shrink-0 items-center gap-1 self-center rounded-full px-2 py-0.5 text-xs font-medium">
              <CrescentMoonBadge className="h-3 w-3" />
              from dream
            </span>
          )}
          <span className="text-text-muted shrink-0 font-mono text-xs tabular-nums">
            {formatTime(entry.timestamp)}
          </span>
        </div>

        {/* Mono secondary detail line */}
        {narrative.detail && (
          <p className="text-text-muted mt-0.5 truncate font-mono text-xs">
            {narrative.detail}
          </p>
        )}
      </div>
    </div>
  );
}

/* ------------------------------------------------------------------ */
/*  Skeleton                                                           */
/* ------------------------------------------------------------------ */

function TimelineSkeleton(): JSX.Element {
  return (
    <div className="space-y-4 p-4" aria-label="Loading audit entries">
      {[1, 2, 3].map((i) => (
        <div key={i}>
          <div className="bg-surface-sunken mb-2 h-4 w-48 animate-pulse rounded" />
          {[1, 2].map((j) => (
            <div
              key={j}
              className="border-border-subtle flex items-center gap-3 border-b px-3 py-2"
            >
              <div className="bg-surface-sunken h-3 w-16 animate-pulse rounded" />
              <div className="bg-surface-sunken h-2 w-2 rounded-full" />
              <div className="bg-surface-sunken h-3 w-24 animate-pulse rounded" />
              <div className="bg-surface-sunken h-3 w-32 animate-pulse rounded" />
            </div>
          ))}
        </div>
      ))}
    </div>
  );
}

/* ------------------------------------------------------------------ */
/*  Main component                                                     */
/* ------------------------------------------------------------------ */

export interface AuditTimelineProps {
  /** Legend entries (from buildLegend) drive the color-dot per row. */
  legendMap: Map<string, string>;
  /** Stable ISO string for the `since` time-window (memoized by caller
   *  to prevent queryKey churn from sinceToISO's new Date() per render). */
  sinceISO?: string | null;
}

export function AuditTimeline({ legendMap, sinceISO }: AuditTimelineProps): JSX.Element {
  const { slug = '' } = useParams<{ slug: string }>();
  const [searchParams] = useSearchParams();
  const filters = decodeFilters(searchParams);
  const auditQuery = useAuditList({
    agent: filters.agent,
    action: filters.action,
    since: sinceISO,
    task_id: filters.task_id,
    limit: 500,
  });
  const queryClient = useQueryClient();

  // Flatten every loaded page BEFORE any client-side narrowing, so day-
  // grouping and filtering span the full loaded set and never reset per page.
  const allEntries = useMemo(
    () => auditQuery.data?.pages.flatMap((p) => p.entries) ?? [],
    [auditQuery.data],
  );

  // Narrow to the active event-class CLIENT-SIDE (AUDIT-02): the right-rail
  // legend filter selects one of the five human classes, which spans several
  // raw event-types, so it can't be a single-`action` API param. Filtering the
  // already-fetched rows keeps the legend counts stable and avoids a refetch.
  const entries = filters.eventClass
    ? allEntries.filter((e) => classOf(e.action) === filters.eventClass)
    : allEntries;

  const { fetchNextPage, hasNextPage, isFetchingNextPage } = auditQuery;

  // Loading
  if (auditQuery.isLoading) return <TimelineSkeleton />;

  // Error
  if (auditQuery.isError) {
    return (
      <div className="flex flex-col items-center justify-center gap-3 p-8 text-center">
        <p className="text-tier-red text-sm">
          Could not load audit entries.
          {auditQuery.error?.message && <> {auditQuery.error.message}</>}
        </p>
        <Button
          size="sm"
          variant="outline"
          onClick={() =>
            queryClient.invalidateQueries({
              queryKey: ['audit', slug],
            })
          }
        >
          Retry
        </Button>
      </div>
    );
  }

  // Empty
  if (entries.length === 0) {
    return (
      <div className="flex h-full items-center justify-center">
        <EmptyState
          title="No audit entries"
          body="No audit entries match the current filters."
        />
      </div>
    );
  }

  // All-clear calm state (zero failures/escalations). This wrapper must carry
  // the SAME bounded-height flex-column chain the normal branch gets from its
  // card parent, so TimelineBody (flex-1 overflow-y-auto) gets a height and its
  // infinite-scroll sentinel can re-intersect — otherwise this branch clips and
  // dies exactly like the normal one did (THR-098 re-open).
  if (isAllClear(entries)) {
    return (
      <div data-testid="all-clear" className="flex h-full min-h-0 flex-col">
        <AllClearBanner />
        <TimelineBody
          entries={entries}
          legendMap={legendMap}
          slug={slug}
          fetchNextPage={fetchNextPage}
          hasNextPage={hasNextPage}
          isFetchingNextPage={isFetchingNextPage}
        />
      </div>
    );
  }

  return (
    <TimelineBody
      entries={entries}
      legendMap={legendMap}
      slug={slug}
      fetchNextPage={fetchNextPage}
      hasNextPage={hasNextPage}
      isFetchingNextPage={isFetchingNextPage}
    />
  );
}

function AllClearBanner(): JSX.Element {
  return (
    <div className="bg-surface border-border-default shadow-pasture-sm mx-4 mt-4 flex shrink-0 items-center gap-3 rounded-lg border p-4">
      <span
        aria-hidden="true"
        className="bg-positive inline-block h-2.5 w-2.5 rounded-full"
      />
      <div>
        <p className="text-text-primary font-display text-sm font-medium">All clear</p>
        <p className="text-text-muted text-xs">
          No failures or escalations in this window.
        </p>
      </div>
    </div>
  );
}

function TimelineBody({
  entries,
  legendMap,
  slug,
  fetchNextPage,
  hasNextPage,
  isFetchingNextPage,
}: {
  entries: AuditEntry[];
  legendMap: Map<string, string>;
  slug: string;
  fetchNextPage: () => void;
  hasNextPage: boolean;
  isFetchingNextPage: boolean;
}): JSX.Element {
  const days = useMemo(() => groupByDay(entries), [entries]);

  // Infinite scroll: observe a bottom sentinel against THIS scroll container
  // (the timeline scrolls inside its own overflow-y-auto box, not the
  // viewport) and load the next older page as it nears view. Re-subscribes
  // when pagination state changes so the moved sentinel is re-observed.
  //
  // Root = sentinel.parentElement (the scroll container) — not scrollRef.current.
  // scrollRef.current can be null during the commit/effect handoff, which would
  // silently fall back to the document viewport and never fire while the user
  // scrolls inside the overflow box (THR-098). parentElement is a DOM property
  // set the moment the sentinel is appended, so it is always correct.
  const scrollRef = useRef<HTMLDivElement | null>(null);
  const sentinelRef = useRef<HTMLDivElement | null>(null);
  useEffect(() => {
    const node = sentinelRef.current;
    if (!node || !hasNextPage) return;
    // Sentinel is a direct child of the overflow-y-auto container —
    // parentElement IS the scroll box. Use it to guarantee the observer
    // root is the scroll container, never the document viewport.
    const root = node.parentElement;
    const obs = new IntersectionObserver(
      (obsEntries) => {
        if (obsEntries[0]?.isIntersecting && !isFetchingNextPage) {
          fetchNextPage();
        }
      },
      { root, rootMargin: '200px' },
    );
    obs.observe(node);
    return () => obs.disconnect();
  }, [fetchNextPage, hasNextPage, isFetchingNextPage]);

  return (
    <div ref={scrollRef} className="flex-1 overflow-y-auto" aria-label="Audit timeline">
      {days.map(({ date, entries: dayEntries }) => (
        <div key={date}>
          <h3 className="text-text-muted bg-surface sticky top-0 z-10 px-4 pt-4 pb-2 text-xs font-medium tracking-wide uppercase">
            {formatDateHeader(date)}
          </h3>
          {dayEntries.map((e) => (
            <TimelineRow
              key={e.id}
              entry={e}
              legendColor={legendMap.get(e.action) ?? 'amber'}
              slug={slug}
            />
          ))}
        </div>
      ))}
      {/* Infinite-scroll sentinel + progress / end-of-list affordances */}
      <div ref={sentinelRef} aria-hidden className="h-1" />
      {isFetchingNextPage && (
        <p className="text-text-muted py-3 text-center text-sm" role="status">
          Loading more…
        </p>
      )}
      {!hasNextPage && entries.length > 0 && (
        <p className="text-text-muted py-4 text-center text-xs">
          End of audit trail
        </p>
      )}
    </div>
  );
}
