/**
 * SchedulePage — read-only list of scheduled work-hours wakes (§4.9 PRD final).
 *
 * - Fetches work-hours list from the daemon's work_hours table.
 * - Groups entries by agent name, sorted by scheduled_for descending.
 * - Each entry shows: agent, local_date, slot, mode, scheduled_for, status,
 *   routine_count, spawned task IDs (IdBadge click-through to Tasks).
 * - Agent names link to the Agents page.
 * - NO authoring controls — no create/edit/delete, no "add wake" form (D6 deferred).
 *
 * States: loading skeleton, calm empty ("No scheduled wakes"), error with Retry.
 */
import { useMemo } from 'react';
import { Link, useParams } from 'react-router-dom';
import { PageHeader } from '@/design-system/patterns/PageHeader';
import { EmptyState } from '@/design-system/patterns/EmptyState';
import { IdBadge } from '@/design-system/patterns/IdBadge';
import { Button } from '@/design-system/primitives/Button';
import { cn } from '@/lib/utils';
import { useWorkHoursList } from '@/hooks/schedule';
import { useQueryClient } from '@tanstack/react-query';
import type { WorkHourRecord } from '@/lib/api/types';

/* ------------------------------------------------------------------ */
/*  Status helpers                                                     */
/* ------------------------------------------------------------------ */

const STATUS_LABEL: Record<string, string> = {
  pending: 'Pending',
  running: 'Running',
  completed: 'Completed',
  failed: 'Failed',
  timeout: 'Timed out',
  skipped: 'Skipped',
};

const STATUS_COLOR: Record<string, string> = {
  pending: 'text-fg-muted',
  running: 'text-accent',
  completed: 'text-feedback-success',
  failed: 'text-tier-red',
  timeout: 'text-feedback-warning',
  skipped: 'text-fg-muted',
};

function formatStatus(status: string): string {
  return STATUS_LABEL[status] ?? status;
}

function formatScheduledFor(iso: string): string {
  return new Date(iso).toLocaleString(undefined, {
    year: 'numeric',
    month: 'short',
    day: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
  });
}

/* ------------------------------------------------------------------ */
/*  Grouping helper                                                    */
/* ------------------------------------------------------------------ */

/** Group entries by agent_name, sorted by agent name, then within each
 *  group most-recent scheduled_for first. */
function groupByAgent(entries: WorkHourRecord[]): { agent: string; entries: WorkHourRecord[] }[] {
  const map = new Map<string, WorkHourRecord[]>();
  for (const e of entries) {
    const bucket = map.get(e.agent_name);
    if (bucket) bucket.push(e);
    else map.set(e.agent_name, [e]);
  }
  return [...map.entries()]
    .sort((a, b) => a[0].localeCompare(b[0]))
    .map(([agent, agentEntries]) => ({
      agent,
      entries: agentEntries.sort(
        (a, b) => b.scheduled_for.localeCompare(a.scheduled_for),
      ),
    }));
}

/* ------------------------------------------------------------------ */
/*  Skeleton                                                           */
/* ------------------------------------------------------------------ */

function ScheduleSkeleton(): JSX.Element {
  return (
    <div className="space-y-4 p-4" aria-label="Loading scheduled wakes">
      {[1, 2, 3].map((i) => (
        <div key={i}>
          <div className="bg-surface-sunken mb-2 h-4 w-32 animate-pulse rounded" />
          {[1, 2].map((j) => (
            <div
              key={j}
              className="border-border-subtle flex items-center gap-3 border-b px-3 py-2"
            >
              <div className="bg-surface-sunken h-3 w-20 animate-pulse rounded" />
              <div className="bg-surface-sunken h-3 w-24 animate-pulse rounded" />
              <div className="bg-surface-sunken h-3 w-16 animate-pulse rounded" />
              <div className="bg-surface-sunken h-3 w-32 animate-pulse rounded" />
            </div>
          ))}
        </div>
      ))}
    </div>
  );
}

/* ------------------------------------------------------------------ */
/*  Wake row                                                           */
/* ------------------------------------------------------------------ */

function WakeRow({ entry, slug }: { entry: WorkHourRecord; slug: string }): JSX.Element {
  return (
    <div className="border-border-subtle hover:bg-surface-raised flex items-center gap-3 border-b px-3 py-2 text-sm transition-colors">
      {/* Local date */}
      <span className="text-fg-muted w-24 shrink-0 font-mono text-xs">
        {entry.local_date}
      </span>

      {/* Slot (cadence label) */}
      <span className="text-fg shrink-0 font-mono text-xs" title={`Slot: ${entry.slot}`}>
        {entry.slot}
      </span>

      {/* Mode */}
      <span className="text-fg-muted shrink-0 text-xs capitalize">
        {entry.mode}
      </span>

      {/* Scheduled for */}
      <span className="text-fg-muted w-40 shrink-0 text-xs">
        {formatScheduledFor(entry.scheduled_for)}
      </span>

      {/* Status */}
      <span className={cn('shrink-0 text-xs font-medium', STATUS_COLOR[entry.status] ?? 'text-fg-muted')}>
        {formatStatus(entry.status)}
      </span>

      {/* Routine count */}
      {entry.routine_count > 0 && (
        <span className="text-fg-muted shrink-0 text-xs">
          {entry.routine_count} {entry.routine_count === 1 ? 'routine' : 'routines'}
        </span>
      )}

      {/* Spacer */}
      <span className="flex-1" />

      {/* Spawned tasks — IdBadge links */}
      {entry.spawned_task_ids.length > 0 && (
        <span className="flex items-center gap-1">
          {entry.spawned_task_ids.map((tid) => (
            <IdBadge
              key={tid}
              kind="task"
              id={tid}
              to={`/orgs/${slug}/tasks/${tid}`}
            />
          ))}
        </span>
      )}
    </div>
  );
}

/* ------------------------------------------------------------------ */
/*  Main component                                                     */
/* ------------------------------------------------------------------ */

export function SchedulePage(): JSX.Element {
  const { slug = '' } = useParams<{ slug: string }>();
  const query = useWorkHoursList({ limit: 100 });
  const queryClient = useQueryClient();

  const entries = useMemo(() => query.data?.work_hours ?? [], [query.data?.work_hours]);

  // Group by agent
  const groups = useMemo(() => groupByAgent(entries), [entries]);

  return (
    <div className="bg-surface-canvas flex h-full flex-col">
      {/* Header */}
      <header className="border-border-subtle border-b p-4">
        <PageHeader
          title="Schedule"
          meta="Per-agent working-hours wakes — when agents run and what they spawn."
        />
        <p className="text-caption text-fg-muted mt-2">
          View-only. Creating named recurring wakes is not available in this release.
        </p>
      </header>

      {/* Loading */}
      {query.isLoading && <ScheduleSkeleton />}

      {/* Error */}
      {query.isError && (
        <div className="flex flex-col items-center justify-center gap-3 p-8 text-center">
          <p className="text-tier-red text-sm">
            Could not load scheduled wakes.
            {query.error?.message && <> {query.error.message}</>}
          </p>
          <Button
            size="sm"
            variant="outline"
            onClick={() =>
              queryClient.invalidateQueries({
                queryKey: ['work-hours-list', slug],
              })
            }
          >
            Retry
          </Button>
        </div>
      )}

      {/* Empty */}
      {!query.isLoading && !query.isError && entries.length === 0 && (
        <div className="flex h-full items-center justify-center">
          <EmptyState
            title="No scheduled wakes"
            body="No working-hours wakes have been scheduled yet."
          />
        </div>
      )}

      {/* Wake list */}
      {!query.isLoading && !query.isError && entries.length > 0 && (
        <div className="flex-1 overflow-y-auto" aria-label="Scheduled wakes">
          {groups.map(({ agent, entries: agentEntries }) => (
            <div key={agent}>
              {/* Agent group header */}
              <h3 className="text-fg-muted bg-surface-sunken sticky top-0 z-10 border-b px-3 py-2 text-xs font-medium tracking-wider uppercase">
                <Link
                  to={`/orgs/${slug}/agents/${agent}`}
                  className="text-accent hover:underline"
                >
                  {agent}
                </Link>
              </h3>
              {agentEntries.map((e) => (
                <WakeRow key={e.work_hour_id} entry={e} slug={slug} />
              ))}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
