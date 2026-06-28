/**
 * WakesView — read-only list of scheduled work-hours wakes (§4.9 PRD final).
 * Direction-A Pasture fidelity pass (THR-030 Leg B Batch 10).
 *
 * RELOCATION (THR-035 consolidation): this list was the standalone "Schedule"
 * surface; it is now the "Wakes" in-page tab of the Work Hours surface,
 * reached at `/orgs/:slug/work-hours?view=wakes` (the WorkHoursTabs strip
 * toggles Overview ↔ Wakes). The rendering below is unchanged from the former
 * SchedulePage — same data model, same fields, same honesty fence; only the
 * component name and the added tab strip differ.
 *
 * - Fetches work-hours list from the daemon's work_hours table.
 * - Groups entries by agent name, each agent in a Pasture card.
 * - Each wake entry shows: local_date, slot, mode, scheduled_for, status pill,
 *   routine_count, spawned task IDs (IdBadge click-through to Tasks), summary,
 *   and error (if present).
 * - Agent names link to the Agents page (font-display heading within card).
 * - NO authoring controls — no create/edit/delete, no "add wake" form (D6 deferred).
 *
 * States: loading skeleton, calm empty ("No scheduled wakes"), error with Retry.
 *
 * Pasture vocabulary:
 *   Cards: bg-surface + border-border-default + shadow-pasture-sm + rounded-lg
 *   Page heading: font-display serif (Newsreader) via PageHeader
 *   Agent names: font-display
 *   IDs / times / counts: font-mono tabular-nums
 *   Status pills: rounded-full bg-accent-soft/text-accent-text (completed/running),
 *     bg-danger-soft/text-feedback-danger (failed/timeout),
 *     bg-surface-sunken/text-text-muted (pending/skipped)
 *   Count eyebrow: text-overline uppercase tracking-wider
 *   Semantic text: text-text-primary / secondary / muted — no hardcoded colors
 *   Calm empty state: EmptyState pattern
 *
 * HONESTY FENCE: renders ONLY fields from WorkHourRecord data model
 * (work_hour_id, agent_name, local_date, slot, mode, scheduled_for,
 * started_at, ended_at, status, routine_count, spawned_task_ids,
 * spawned_task_count, summary, error, session_id, transcript_path, created_at).
 * OMITTED (no backing field): week grid / 24h visual timeline /
 * "While you were away" feed / calm toggles / schedule-health metrics /
 * run history / next-run predictions — none of these fields exist on
 * WorkHourRecord. The a-schedule.html reference elements without data-model
 * backing are documented here per Confusion-Protocol, not fabricated.
 *
 * THR-030 SCHED-02 (presentation-only): header restyled to the Direction-A
 * a-schedule reference — uppercase eyebrow + Newsreader serif title, matching
 * the Tasks/KB/Audit page-header treatment. The a-schedule TIMEZONE chip is
 * intentionally OMITTED: the working-hours timezone is not exposed on any
 * web-consumed payload (the /work-hours list response carries no tz, and the
 * only timezone on the wire is the *dreaming* schedule's, a different
 * schedule), so per the HONESTY FENCE it is deferred rather than fabricated.
 */
import { useMemo } from 'react';
import { Link, useParams } from 'react-router-dom';
import { EmptyState } from '@/design-system/patterns/EmptyState';
import { IdBadge } from '@/design-system/patterns/IdBadge';
import { Button } from '@/design-system/primitives/Button';
import { cn } from '@/lib/utils';
import { useWorkHoursList } from '@/hooks/schedule';
import { useQueryClient } from '@tanstack/react-query';
import type { WorkHourRecord } from '@/lib/api/types';
import { WorkHoursTabs } from './components';

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

function statusPill(status: string): string {
  switch (status) {
    case 'completed':
    case 'running':
      return 'bg-accent-soft text-accent-text';
    case 'failed':
    case 'timeout':
      return 'bg-danger-soft text-feedback-danger';
    case 'pending':
    case 'skipped':
    default:
      return 'bg-surface-sunken text-text-muted';
  }
}

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
/*  Skeleton — Pasture card-shaped                                     */
/* ------------------------------------------------------------------ */

function ScheduleSkeleton(): JSX.Element {
  return (
    <div className="flex flex-col gap-4 p-4" aria-label="Loading scheduled wakes">
      {[1, 2].map((i) => (
        <div
          key={i}
          className="bg-surface border-border-default shadow-pasture-sm animate-pulse space-y-3 rounded-lg border p-4"
        >
          <div className="bg-surface-sunken h-4 w-24 rounded" />
          {[1, 2, 3].map((j) => (
            <div key={j} className="flex items-center gap-3">
              <div className="bg-surface-sunken h-3 w-16 rounded" />
              <div className="bg-surface-sunken h-3 w-12 rounded" />
              <div className="bg-surface-sunken h-3 w-20 rounded" />
              <div className="bg-surface-sunken h-3 w-28 rounded" />
            </div>
          ))}
        </div>
      ))}
    </div>
  );
}

/* ------------------------------------------------------------------ */
/*  Wake row — within a per-agent Pasture card                         */
/* ------------------------------------------------------------------ */

function WakeRow({ entry, slug }: { entry: WorkHourRecord; slug: string }): JSX.Element {
  return (
    <div className="hover:bg-surface-hover flex items-center gap-3 rounded px-2 py-1.5 text-sm transition-colors">
      {/* Status pill */}
      <span
        className={cn(
          'shrink-0 rounded-full px-2 py-0.5 text-[10px] font-medium uppercase tracking-wide',
          statusPill(entry.status),
        )}
      >
        {formatStatus(entry.status)}
      </span>

      {/* Local date */}
      <span className="text-text-muted shrink-0 font-mono text-xs tabular-nums">
        {entry.local_date}
      </span>

      {/* Slot */}
      <span className="text-text-secondary shrink-0 font-mono text-xs tabular-nums">
        {entry.slot}
      </span>

      {/* Mode */}
      <span className="text-text-muted shrink-0 text-xs capitalize">
        {entry.mode}
      </span>

      {/* Scheduled for */}
      <span className="text-text-muted shrink-0 font-mono text-xs tabular-nums">
        {formatScheduledFor(entry.scheduled_for)}
      </span>

      {/* Routine count */}
      {entry.routine_count > 0 && (
        <span className="text-text-muted shrink-0 font-mono text-xs tabular-nums">
          {entry.routine_count} {entry.routine_count === 1 ? 'routine' : 'routines'}
        </span>
      )}

      {/* Summary */}
      {entry.summary && (
        <span
          className="text-text-secondary shrink-0 max-w-[16rem] truncate text-xs"
          title={entry.summary}
        >
          {entry.summary}
        </span>
      )}

      {/* Error */}
      {entry.error && (
        <span
          className="text-feedback-danger shrink-0 max-w-[16rem] truncate text-xs"
          title={entry.error}
        >
          {entry.error}
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
/*  Agent group card — Pasture card with font-display header          */
/* ------------------------------------------------------------------ */

function AgentGroupCard({
  agent,
  entries,
  slug,
}: {
  agent: string;
  entries: WorkHourRecord[];
  slug: string;
}): JSX.Element {
  return (
    <div className="bg-surface border-border-default shadow-pasture-sm overflow-hidden rounded-lg border">
      {/* Card header — agent name + count eyebrow */}
      <div className="border-border-default flex items-center justify-between border-b px-4 py-3">
        <Link
          to={`/orgs/${slug}/agents/${agent}`}
          className="text-text-primary font-display text-base hover:underline"
        >
          {agent}
        </Link>
        <span className="text-text-muted font-mono text-xs tabular-nums">
          {entries.length} wake{entries.length !== 1 ? 's' : ''}
        </span>
      </div>

      {/* Wake entries */}
      <div className="divide-border-subtle divide-y px-2 py-1">
        {entries.map((e) => (
          <WakeRow key={e.work_hour_id} entry={e} slug={slug} />
        ))}
      </div>
    </div>
  );
}

/* ------------------------------------------------------------------ */
/*  Main component                                                     */
/* ------------------------------------------------------------------ */

export function WakesView(): JSX.Element {
  const { slug: orgSlug } = useParams<{ slug: string }>();
  const query = useWorkHoursList({ limit: 100 });
  const queryClient = useQueryClient();

  const entries = useMemo(() => query.data?.work_hours ?? [], [query.data?.work_hours]);

  // Group by agent
  const groups = useMemo(() => groupByAgent(entries), [entries]);

  return (
    <div className="flex h-full flex-col">
      {/* Header — SCHED-02: Direction-A uppercase eyebrow + Newsreader serif
          title (a-schedule reference), matching the Tasks/KB/Audit surfaces. */}
      <header className="border-border-default border-b p-4">
        <p className="text-text-muted text-xs font-medium tracking-wide uppercase">
          Working hours · When the org is awake
        </p>
        <h1 className="font-display text-display text-text-primary mt-1 font-medium">
          Give your agents a rhythm.
        </h1>
        <p className="text-caption text-text-muted mt-1">
          Per-agent working-hours wakes — when agents run and what they spawn.
        </p>
        <p className="text-text-muted mt-2 text-xs">
          View-only. Creating named recurring wakes is not available in this release.
        </p>
      </header>

      {/* Sub-nav — Overview (config) ↔ Wakes (this view). */}
      <WorkHoursTabs slug={orgSlug} active="wakes" />

      {/* Loading */}
      {query.isLoading && <ScheduleSkeleton />}

      {/* Error */}
      {query.isError && (
        <div className="flex flex-col items-center justify-center gap-3 p-8 text-center">
          <p className="text-feedback-danger text-sm">
            Could not load scheduled wakes.
            {query.error?.message && <> {query.error.message}</>}
          </p>
          <Button
            size="sm"
            variant="outline"
            onClick={() =>
              queryClient.invalidateQueries({
                queryKey: ['work-hours-list', orgSlug],
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

      {/* Wake list — agent cards */}
      {!query.isLoading && !query.isError && entries.length > 0 && (
        <div className="flex-1 overflow-y-auto" aria-label="Scheduled wakes">
          {/* Count eyebrow */}
          <p className="text-overline text-text-secondary mx-4 mt-4 mb-2 tracking-wider uppercase">
            <span className="font-mono tabular-nums">{entries.length}</span> wake{entries.length !== 1 ? 's' : ''} across <span className="font-mono tabular-nums">{groups.length}</span> agent{groups.length !== 1 ? 's' : ''}
          </p>

          <div className="space-y-4 p-4">
            {groups.map(({ agent, entries: agentEntries }) => (
              <AgentGroupCard
                key={agent}
                agent={agent}
                entries={agentEntries}
                slug={orgSlug ?? ''}
              />
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
