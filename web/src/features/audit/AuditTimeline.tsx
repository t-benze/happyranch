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
import { useMemo } from 'react';
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
  return new Date(iso).toLocaleTimeString(undefined, {
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
  });
}

function formatDateHeader(dateStr: string): string {
  // YYYY-MM-DD from ISO timestamp, stable across locales/engines.
  return dateStr;
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
    <div className="border-border-subtle hover:bg-surface-raised flex gap-3 border-b px-3 py-2.5 text-sm transition-colors">
      {/* Color-coded event dot */}
      <span
        aria-hidden="true"
        className={cn(
          'mt-[0.4rem] inline-block h-2 w-2 shrink-0 rounded-full',
          DOT_COLOR_CLASS[legendColor as keyof typeof DOT_COLOR_CLASS] ?? 'bg-fg-muted',
        )}
      />

      <div className="min-w-0 flex-1">
        {/* Narrative sentence + timestamp */}
        <div className="flex items-baseline gap-3">
          <p className="text-text-secondary min-w-0 flex-1 leading-snug">
            {narrative.segments.map((seg, i) => (
              <Segment key={i} seg={seg} slug={slug} />
            ))}
          </p>
          {hasDream && <CrescentMoonBadge className="h-3 w-3 shrink-0 self-center" />}
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

  const entries = auditQuery.data?.entries ?? [];

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

  // All-clear calm state (zero failures/escalations)
  if (isAllClear(entries)) {
    return (
      <div data-testid="all-clear">
        <AllClearBanner />
        <TimelineBody entries={entries} legendMap={legendMap} slug={slug} />
      </div>
    );
  }

  return <TimelineBody entries={entries} legendMap={legendMap} slug={slug} />;
}

function AllClearBanner(): JSX.Element {
  return (
    <div className="bg-surface border-border-default shadow-pasture-sm mx-4 mt-4 flex items-center gap-3 rounded-lg border p-4">
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
}: {
  entries: AuditEntry[];
  legendMap: Map<string, string>;
  slug: string;
}): JSX.Element {
  const days = useMemo(() => groupByDay(entries), [entries]);

  return (
    <div className="flex-1 overflow-y-auto" aria-label="Audit timeline">
      {days.map(({ date, entries: dayEntries }) => (
        <div key={date}>
          <h3 className="text-text-secondary bg-surface-sunken border-border-subtle font-display sticky top-0 z-10 border-b px-4 py-2 text-sm font-medium">
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
    </div>
  );
}
