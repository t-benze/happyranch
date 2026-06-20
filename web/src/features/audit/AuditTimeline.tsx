/**
 * AuditTimeline — day-grouped reverse-chronological timeline.
 *
 * Each entry renders:
 *   time · agent · event-class colored dot · action label · optional
 *   executor / token cost / dream marker · object-ID badges (click-through).
 *
 * States: loading skeleton, empty ("No audit entries"), all-clear ("All clear
 * — no failures or escalations"), error with retry.
 */
import { useMemo } from 'react';
import { Link, useParams, useSearchParams } from 'react-router-dom';
import { CrescentMoonBadge } from '@/design-system/patterns/CrescentMoonBadge';
import { IdBadge } from '@/design-system/patterns/IdBadge';
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

function tokensFromPayload(p: Record<string, unknown>): number | undefined {
  const tu = p['token_usage'];
  if (tu && typeof tu === 'object' && 'total' in tu) {
    const t = (tu as Record<string, unknown>).total;
    if (typeof t === 'number') return t;
  }
  const tc = p['token_count'];
  return typeof tc === 'number' ? tc : undefined;
}

function executorFromPayload(p: Record<string, unknown>): string | undefined {
  // executor is ONLY from payload.executor — agent_session_id is a session
  // identifier, not the executor/provider.  Fabricating executor attribution
  // from it violates the provenance honesty lens.
  const ex = p['executor'];
  if (typeof ex === 'string') return ex;
  return undefined;
}

function formatTokens(n: number): string {
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`;
  if (n >= 1_000) return `${(n / 1_000).toFixed(1)}K`;
  return String(n);
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
  const tokens = tokensFromPayload(entry.payload);
  const executor = executorFromPayload(entry.payload);
  const hasDream = !!entry._thread_dream_id;

  // Determine target detail path from task_id scope prefix (task/thread only;
  // job badges are rendered separately from payload.script_request_id).
  const scopeLink = useMemo(() => {
    if (!entry.task_id) return null;
    const tid = entry.task_id;
    if (tid.startsWith('TASK-')) return { to: `/orgs/${slug}/tasks/${tid}`, kind: 'task' as const };
    if (tid.startsWith('THR-')) return { to: `/orgs/${slug}/threads/${tid}`, kind: 'thread' as const };
    return null;
  }, [entry.task_id, slug]);

  return (
    <div className="border-border-subtle hover:bg-surface-raised flex items-center gap-3 border-b px-3 py-2 text-sm transition-colors">
      {/* Time */}
      <span className="text-fg-muted w-20 shrink-0 font-mono text-xs tabular-nums">
        {formatTime(entry.timestamp)}
      </span>

      {/* Color-coded event dot */}
      <span
        aria-hidden="true"
        className={cn('inline-block h-2 w-2 shrink-0 rounded-full', DOT_COLOR_CLASS[legendColor as keyof typeof DOT_COLOR_CLASS] ?? 'bg-fg-muted')}
      />

      {/* Agent */}
      {entry.agent && (
        <span className="text-fg w-28 shrink-0 truncate">{entry.agent}</span>
      )}

      {/* Action label */}
      <span className="text-fg-muted shrink-0 font-mono text-xs">{entry.action}</span>

      {/* Executor */}
      {executor && (
        <span className="text-fg-muted shrink-0 text-xs" title={executor}>
          {executor}
        </span>
      )}

      {/* Token cost */}
      {tokens != null && tokens > 0 && (
        <span className="text-fg-muted shrink-0 font-mono text-xs tabular-nums">
          {formatTokens(tokens)} tok
        </span>
      )}

      {/* Dream marker */}
      {hasDream && <CrescentMoonBadge className="h-3 w-3" />}

      {/* Spacer */}
      <span className="flex-1" />

      {/* Job ID badge (click-through) — for job_* actions, the real
          job id lives in payload.script_request_id, while task_id is the
          parent task that owns the job.  Both are linked separately. */}
      {entry.action.startsWith('job_') && (() => {
        const jobId = entry.payload.script_request_id as string | undefined;
        if (jobId) {
          return (
            <Link
              to={`/orgs/${slug}/jobs/${jobId}`}
              className="text-id-job font-mono text-xs hover:underline"
            >
              {jobId}
            </Link>
          );
        }
        return null;
      })()}

      {/* Object ID badge (click-through) — task/thread link */}
      {scopeLink && (
        <IdBadge kind={scopeLink.kind} id={entry.task_id!} to={scopeLink.to} />
      )}
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
