/**
 * JobsPage — the approval-queue LIST surface ("Jobs · the approval queue").
 *
 * REINSTATED per founder ruling (THR-030 seq 91, TASK-907): the standalone
 * Jobs surface — previously retired per PRD §4.13/Q6 — is brought back as the
 * founder's approval queue. The command is the hero of every row; cards route
 * to the existing JobDetailPage where the two-step approve/review flow lives.
 *
 * PRESENTATION-ONLY, built off data we already have (GET /jobs/). Honesty
 * fence (per the build brief): NO BlockBadge (the design's "needs credential"
 * / "flagged for review" tags need a founder-vault `env:[{key,founderHeld}]`
 * field that does not exist on JobRecord), and NO inline approve/reject — the
 * row only NAVIGATES; the decision lives on the detail surface.
 */
import { useMemo, useState } from 'react';
import { Link, useParams } from 'react-router-dom';
import { ContentWrap } from '@/design-system/layouts/ContentWrap/ContentWrap';
import { TONE_CLASS, toneClass } from '@/design-system/patterns/semanticTone';
import { useJobsList } from '@/hooks/jobs';
import type { JobRecord, JobStatus } from '@/lib/api/types';

// ── Helpers ──────────────────────────────────────────────────────────

/** Relative age of an ISO timestamp ("just now" / "12m" / "3h" / "2d"). */
function relativeAge(iso: string): string {
  const ms = Date.now() - new Date(iso).getTime();
  const min = Math.round(ms / 60000);
  if (min < 1) return 'just now';
  if (min < 60) return `${min}m`;
  const hr = Math.round(min / 60);
  if (hr < 24) return `${hr}h`;
  const d = Math.round(hr / 24);
  return `${d}d`;
}

/** Elapsed wall-clock since a running job's started_at ("6m 02s" / "1h 04m"). */
function elapsedSince(iso: string): string {
  const totalSec = Math.max(0, Math.floor((Date.now() - new Date(iso).getTime()) / 1000));
  const min = Math.floor(totalSec / 60);
  if (min < 60) return `${min}m ${String(totalSec % 60).padStart(2, '0')}s`;
  const hr = Math.floor(min / 60);
  return `${hr}h ${String(min % 60).padStart(2, '0')}m`;
}

/** cwd shown on a row — resolved cwd is more precise; fall back to the hint. */
function jobCwd(job: JobRecord): string | null {
  return job.cwd_resolved ?? job.cwd_hint;
}

// ── Status pill ──────────────────────────────────────────────────────

// Pure lifecycle, system-level. Mirrors the design's JOB_STATUS kinds
// (warn/run/ok/bad/mute2) onto our semantic tokens. StatusBadge can't be
// reused here: it has no `running`/`rejected` member, so it would render an
// undefined style for two of the five real JobStatus values.
const STATUS_PILL: Record<JobStatus, string> = {
  pending: 'text-status-archiving bg-tier-yellow-tint',
  running: 'text-feedback-info bg-info-soft',
  completed: 'text-status-open bg-tier-green-tint',
  failed: 'text-status-abandoned bg-tier-red-tint',
  rejected: 'text-text-muted bg-surface-sunken border border-border-default',
};

function JobStatusPill({ status }: { status: JobStatus }): JSX.Element {
  return (
    <span
      className={`text-mono-sm inline-flex items-center rounded-full px-2 py-0.5 font-medium ${STATUS_PILL[status]}`}
    >
      {status}
    </span>
  );
}

// ── Status-group headers ─────────────────────────────────────────────

// Lifecycle order for the list's status-group sections — founder-blocking
// pending first. Each header carries a colored dot + a count; the dot reuses
// the same semantic status tokens as the row pills (no new tokens, no raw hex).
const STATUS_GROUP_ORDER: JobStatus[] = ['pending', 'running', 'completed', 'failed', 'rejected'];

const STATUS_GROUP_META: Record<JobStatus, { label: string; dot: string }> = {
  pending: { label: 'Pending', dot: 'bg-status-archiving' },
  running: { label: 'Running', dot: 'bg-feedback-info' },
  completed: { label: 'Completed', dot: 'bg-status-open' },
  failed: { label: 'Failed', dot: 'bg-status-abandoned' },
  rejected: { label: 'Rejected', dot: 'bg-text-muted' },
};

/**
 * Status-group section header — a colored lifecycle dot, the status label, and
 * the count of rows in the group. Organizes the list into status sections
 * without removing the filter rail (which narrows the fetched set these groups
 * render over). Sticks to the top of the scroll area as its group scrolls past.
 */
function StatusGroupHeader({ status, count }: { status: JobStatus; count: number }): JSX.Element {
  const { label, dot } = STATUS_GROUP_META[status];
  return (
    <div className="bg-surface-canvas border-border-default text-text-secondary sticky top-0 z-10 flex items-center gap-2 border-b px-4 py-2">
      <span aria-hidden="true" className={`inline-block h-2 w-2 shrink-0 rounded-full ${dot}`} />
      <span className="text-xs font-semibold tracking-wider uppercase">{label}</span>
      <span className="text-mono-sm text-text-muted tabular-nums">{count}</span>
    </div>
  );
}

// ── Filter rail ──────────────────────────────────────────────────────

type StatusFilter = 'all' | JobStatus;
type ReviewFilter = 'all' | 'required' | 'auto';

const STATUS_OPTIONS: { key: StatusFilter; label: string }[] = [
  { key: 'all', label: 'All' },
  { key: 'pending', label: 'Pending' },
  { key: 'running', label: 'Running' },
  { key: 'completed', label: 'Completed' },
  { key: 'failed', label: 'Failed' },
  { key: 'rejected', label: 'Rejected' },
];

const REVIEW_OPTIONS: { key: ReviewFilter; label: string }[] = [
  { key: 'all', label: 'All' },
  { key: 'required', label: 'Review required' },
  { key: 'auto', label: 'Auto-run' },
];

function FilterRow({
  label,
  count,
  active,
  onSelect,
}: {
  label: string;
  count: number;
  active: boolean;
  onSelect: () => void;
}): JSX.Element {
  const disabled = count === 0;
  const base =
    'flex items-center gap-2 border-l-2 px-4 py-1.5 text-left text-sm transition-colors';
  const state = active
    ? 'border-accent-default bg-surface-sunken text-text-primary'
    : disabled
      ? 'border-transparent text-text-muted'
      : 'border-transparent text-text-secondary hover:bg-surface-sunken hover:text-text-primary';
  return (
    <button
      type="button"
      disabled={disabled}
      aria-pressed={active}
      onClick={onSelect}
      className={`${base} ${state} ${disabled ? 'cursor-default opacity-40' : ''}`}
    >
      <span className="flex-1">{label}</span>
      <span className="text-mono-sm text-text-muted tabular-nums">{count}</span>
    </button>
  );
}

function FilterGroup({ label, children }: { label: string; children: React.ReactNode }): JSX.Element {
  return (
    <div className="flex flex-col">
      <div className="text-mono-sm text-text-muted px-4 pb-1.5 font-medium tracking-wider uppercase">
        {label}
      </div>
      {children}
    </div>
  );
}

// ── Job row ──────────────────────────────────────────────────────────

/**
 * Command-forward job card. The verbatim script_text is the hero; the row
 * NAVIGATES to the existing JobDetailPage on click (no inline decision —
 * approve/reject lives on the detail surface).
 */
function JobRow({ job, to }: { job: JobRecord; to: string }): JSX.Element {
  const cwd = jobCwd(job);
  return (
    <Link
      to={to}
      className={`hover:bg-surface-sunken border-border-default flex items-center gap-4 border-b px-4 py-3 transition-colors ${
        job.status === 'rejected' ? 'opacity-70 hover:opacity-100' : ''
      }`}
    >
      <div className="flex min-w-0 flex-1 flex-col gap-1.5">
        <div className="flex flex-wrap items-center gap-2">
          <span className="text-mono-sm text-accent-text tracking-wide">{job.id}</span>
          <JobStatusPill status={job.status} />
          {job.review_required ? (
            <span className="text-mono-sm text-text-muted border-border-default rounded border px-1.5 py-0.5 tracking-wider uppercase">
              review
            </span>
          ) : (
            <span className="text-mono-sm text-accent-text border-border-default rounded border px-1.5 py-0.5 tracking-wider uppercase">
              auto-run
            </span>
          )}
          {job.status === 'running' && job.started_at && (
            <span className="text-mono-sm text-feedback-info">running · {elapsedSince(job.started_at)}</span>
          )}
        </div>
        <code
          className={`text-text-primary block truncate font-mono text-sm ${
            job.status === 'rejected' ? 'line-through' : ''
          }`}
        >
          {job.script_text}
        </code>
        <div className="text-mono-sm text-text-muted flex items-center gap-2">
          <span className="text-text-secondary">{job.agent_name}</span>
          <span aria-hidden="true">·</span>
          <span className="text-accent-text">{job.task_id}</span>
          {cwd && (
            <>
              <span aria-hidden="true">·</span>
              <span className="truncate">{cwd}</span>
            </>
          )}
          <span className="flex-1" />
          <span>{relativeAge(job.created_at)}</span>
        </div>
      </div>

      {/* Static lifecycle state — terminal/running rows only. Pending rows show
          NO inline control (the decision lives on the detail surface). */}
      <div className="shrink-0">
        {job.status === 'running' && (
          <span className="text-mono-sm text-feedback-info bg-info-soft rounded px-2.5 py-1">running…</span>
        )}
        {job.status === 'completed' && (
          <span
            className={`text-mono-sm inline-flex items-center rounded-full px-2.5 py-0.5 font-medium ${toneClass(`exit ${job.exit_code ?? 0}`)}`}
          >
            exit {job.exit_code ?? 0}
          </span>
        )}
        {job.status === 'failed' && (
          <span
            className={`text-mono-sm inline-flex items-center rounded-full px-2.5 py-0.5 font-medium ${TONE_CLASS.danger}`}
          >
            {job.exit_code != null ? `exit ${job.exit_code}` : 'failed'}
          </span>
        )}
        {job.status === 'rejected' && (
          <span className="text-mono-sm text-text-muted rounded px-2.5 py-1">rejected</span>
        )}
      </div>
    </Link>
  );
}

// ── List header ──────────────────────────────────────────────────────

/** The running-total of founder-blocking decisions (pending jobs). */
function ListHeader({ pendingCount }: { pendingCount: number }): JSX.Element {
  return (
    <div className="border-border-default flex h-12 shrink-0 items-center border-b px-4">
      {pendingCount > 0 ? (
        <span className="text-status-archiving flex items-center gap-2 text-sm font-medium">
          <span aria-hidden="true" className="bg-status-archiving inline-block h-2 w-2 rounded-full" />
          {pendingCount} waiting on you
        </span>
      ) : (
        <span className="text-accent-text flex items-center gap-2 text-sm">
          <span aria-hidden="true" className="bg-accent-default inline-block h-2 w-2 rounded-full" />
          Queue clear · nothing waiting on you
        </span>
      )}
    </div>
  );
}

// ── Page ─────────────────────────────────────────────────────────────

export function JobsPage(): JSX.Element {
  const { slug } = useParams<{ slug: string }>();
  // status:'all' is REQUIRED — GET /jobs/ defaults to status=pending, which
  // would hide every non-pending job and break the live filter counts.
  const query = useJobsList({ status: 'all', limit: 200 });
  const [statusFilter, setStatusFilter] = useState<StatusFilter>('all');
  const [reviewFilter, setReviewFilter] = useState<ReviewFilter>('all');

  const jobs = useMemo<JobRecord[]>(() => query.data?.jobs ?? [], [query.data]);

  // Live counts over the full fetched set (independent of the active filters,
  // matching the design's static count derivations).
  const statusCounts = useMemo(() => {
    const c: Record<StatusFilter, number> = {
      all: jobs.length,
      pending: 0,
      running: 0,
      completed: 0,
      failed: 0,
      rejected: 0,
    };
    for (const j of jobs) c[j.status] += 1;
    return c;
  }, [jobs]);

  const reviewCounts = useMemo(() => {
    const required = jobs.filter((j) => j.review_required).length;
    return { all: jobs.length, required, auto: jobs.length - required };
  }, [jobs]);

  const visible = useMemo(
    () =>
      jobs.filter((j) => {
        if (statusFilter !== 'all' && j.status !== statusFilter) return false;
        if (reviewFilter === 'required' && !j.review_required) return false;
        if (reviewFilter === 'auto' && j.review_required) return false;
        return true;
      }),
    [jobs, statusFilter, reviewFilter],
  );

  return (
    <div className="bg-surface-canvas flex h-full">
      {/* Filter rail */}
      <aside
        aria-label="Job filters"
        className="border-border-default flex w-48 shrink-0 flex-col gap-6 overflow-y-auto border-r py-5"
      >
        <FilterGroup label="Status">
          {STATUS_OPTIONS.map((opt) => (
            <FilterRow
              key={opt.key}
              label={opt.label}
              count={statusCounts[opt.key]}
              active={statusFilter === opt.key}
              onSelect={() => setStatusFilter(opt.key)}
            />
          ))}
        </FilterGroup>
        <FilterGroup label="Review">
          {REVIEW_OPTIONS.map((opt) => (
            <FilterRow
              key={opt.key}
              label={opt.label}
              count={reviewCounts[opt.key]}
              active={reviewFilter === opt.key}
              onSelect={() => setReviewFilter(opt.key)}
            />
          ))}
        </FilterGroup>
      </aside>

      {/* Main list column. EM ruling (THR-099 jobs, flag #2): KEEP the filter
          rail; cap the main list content at the shared 1180 `max-w-content`
          centered. The pinned ListHeader stays a full-width status toolbar
          (fixed-height chrome, a single left status pill — not columns needing
          row-alignment); the scrolling job list is capped by <ContentWrap>,
          which owns the h-full overflow-y-auto scroll surface (26px pad, 1180
          cap) — mirroring the tasks scroll-body cap. */}
      <main className="flex min-w-0 flex-1 flex-col">
        <ListHeader pendingCount={statusCounts.pending} />
        <div className="min-h-0 flex-1">
          <ContentWrap>
          {query.isLoading ? (
            <p className="text-text-muted py-12 text-center text-sm">Loading jobs…</p>
          ) : query.isError ? (
            <p className="text-text-muted py-12 text-center text-sm">Could not load jobs.</p>
          ) : visible.length === 0 ? (
            <p className="text-text-muted py-12 text-center text-sm">
              {jobs.length === 0 ? 'No jobs yet.' : 'No jobs match the current filters.'}
            </p>
          ) : (
            STATUS_GROUP_ORDER.map((status) => {
              const rows = visible.filter((j) => j.status === status);
              if (rows.length === 0) return null;
              return (
                <section key={status} aria-label={STATUS_GROUP_META[status].label}>
                  <StatusGroupHeader status={status} count={rows.length} />
                  <ul>
                    {rows.map((job) => (
                      <li key={job.id}>
                        <JobRow job={job} to={slug ? `/orgs/${slug}/jobs/${job.id}` : '#'} />
                      </li>
                    ))}
                  </ul>
                </section>
              );
            })
          )}
          </ContentWrap>
        </div>
      </main>
    </div>
  );
}
