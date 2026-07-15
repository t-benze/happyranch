/**
 * JobsPage — the approval-queue LIST surface, styled to the Direction-A
 * `a-jobs` mockup (jobs-design.png): an uppercase eyebrow + serif display
 * title, an amber "needs you" callout, a column-header row, and the jobs
 * rendered as elevated CARDS grouped under colored-dot status section headers.
 *
 * IA fidelity over EXISTING data (TASK-2992, THR-099 PR1/3). Every field is
 * read straight off JobRecord (GET /jobs/) — id, title, script_text,
 * agent_name, task_id, status, exit_code, review_required, created_at. NO new
 * fields, NO new routes: the card only NAVIGATES to the existing JobDetailPage
 * where the two-step approve/reject flow lives (JobDetail is PR3, untouched).
 *
 * Honesty fence (unchanged from the reinstatement brief): NO BlockBadge / no
 * "needs credential" tag — that would need a founder-vault `env` field that
 * does not exist on JobRecord — and NO inline approve/reject on the row. The
 * mockup's info-annotation bar ("needs credential #204 … JobRecord.env") is a
 * DESIGN NOTE documenting that same absent field, not shippable product copy,
 * so it is intentionally not reproduced.
 */
import { useMemo } from 'react';
import { Lock } from 'lucide-react';
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

/** Two-letter avatar initials from an agent name (dev_agent → "DA"). Derived
 *  client-side — JobRecord carries no role/avatar field. Kept local (not
 *  imported from features/agents) to respect the web no-restricted-imports
 *  cross-feature rule. */
function agentInitials(name: string): string {
  const parts = name.split(/[_\s-]+/).filter(Boolean);
  const letters =
    parts.length >= 2 ? parts[0][0] + parts[1][0] : (parts[0] ?? name).slice(0, 2);
  return letters.toUpperCase();
}

// One shared row shape drives the column-header row AND every card so the five
// columns line up exactly: JOB · COMMAND(flex) · REQUESTED BY · TASK · OPENED.
// A flex row with NAMED width utilities on the four fixed columns (no arbitrary
// grid-template): w-26 ≈ 6.5rem, w-44 = 11rem, w-20 = 5rem, while COMMAND
// flexes to fill (flex-1 min-w-0).
const CARD_ROW = 'flex items-center gap-4';
const CARD_COL = {
  job: 'w-26 shrink-0',
  command: 'min-w-0 flex-1',
  requestedBy: 'w-44 shrink-0',
  task: 'w-26 shrink-0',
  opened: 'w-20 shrink-0',
};

// ── Status groups ────────────────────────────────────────────────────

// Lifecycle order for the status-group sections — founder-blocking pending
// first. Each header carries a colored dot (same semantic status tokens as the
// row outcome pills — no new tokens, no raw hex) + the group count.
const STATUS_GROUP_ORDER: JobStatus[] = ['pending', 'running', 'completed', 'failed', 'rejected'];

const STATUS_GROUP_META: Record<JobStatus, { label: string; dot: string }> = {
  pending: { label: 'Awaiting your approval', dot: 'bg-status-archiving' },
  running: { label: 'Running', dot: 'bg-feedback-info' },
  completed: { label: 'Completed', dot: 'bg-status-open' },
  failed: { label: 'Failed', dot: 'bg-status-abandoned' },
  rejected: { label: 'Rejected', dot: 'bg-text-muted' },
};

/** Colored lifecycle dot + status label + per-group count. */
function StatusGroupHeader({ status, count }: { status: JobStatus; count: number }): JSX.Element {
  const { label, dot } = STATUS_GROUP_META[status];
  return (
    <div className="flex items-center gap-2 px-1">
      <span aria-hidden="true" className={`inline-block h-2 w-2 shrink-0 rounded-full ${dot}`} />
      <span className="text-text-primary text-sm font-medium">{label}</span>
      <span className="text-mono-sm text-text-muted tabular-nums">{count}</span>
    </div>
  );
}

// ── Row bits ─────────────────────────────────────────────────────────

/** Green "NEEDS REVIEW" pill — only on pending, still-gated jobs. */
function NeedsReviewPill(): JSX.Element {
  return (
    <span className="text-mono-sm text-status-open bg-tier-green-tint inline-flex shrink-0 items-center rounded-full px-2 py-0.5 font-medium tracking-wide uppercase">
      needs review
    </span>
  );
}

/** Requested-by avatar — green initial chip (no backend role field). */
function AgentAvatar({ name }: { name: string }): JSX.Element {
  return (
    <span
      aria-hidden="true"
      className="bg-accent-soft text-accent-text flex h-7 w-7 shrink-0 items-center justify-center rounded-full text-xs font-semibold"
    >
      {agentInitials(name)}
    </span>
  );
}

/**
 * OPENED column — a lifecycle-tinted outcome pill for terminal/running jobs,
 * or the relative age for a pending job. Outcome colours read the shared
 * semanticTone vocabulary (exit 0 green / non-zero red) — Batch 2 settled that.
 */
function OutcomeCell({ job }: { job: JobRecord }): JSX.Element {
  const pill = 'text-mono-sm inline-flex items-center rounded-full px-2.5 py-0.5 font-medium';
  switch (job.status) {
    case 'running':
      return (
        <span className={`${pill} text-feedback-info bg-info-soft gap-1.5`}>
          <span aria-hidden="true" className="bg-feedback-info h-1.5 w-1.5 rounded-full" />
          running
        </span>
      );
    case 'completed':
      return <span className={`${pill} ${toneClass(`exit ${job.exit_code ?? 0}`)}`}>exit {job.exit_code ?? 0}</span>;
    case 'failed':
      return (
        <span className={`${pill} ${TONE_CLASS.danger}`}>
          {job.exit_code != null ? `exit ${job.exit_code}` : 'failed'}
        </span>
      );
    case 'rejected':
      return <span className="text-mono-sm text-text-muted">rejected</span>;
    default:
      return <span className="text-mono-sm text-text-muted tabular-nums">{relativeAge(job.created_at)}</span>;
  }
}

/**
 * A job as an elevated card. The whole card NAVIGATES to the existing
 * JobDetailPage (no inline decision). Columns match the header row exactly.
 */
function JobCard({ job, to }: { job: JobRecord; to: string }): JSX.Element {
  return (
    <Link
      to={to}
      className={`${CARD_ROW} bg-surface-raised border-border-default hover:shadow-pasture shadow-pasture-sm rounded-lg border px-5 py-4 transition-shadow ${
        job.status === 'rejected' ? 'opacity-70 hover:opacity-100' : ''
      }`}
    >
      {/* JOB */}
      <span className={`${CARD_COL.job} text-mono-sm text-accent-text tracking-wide`}>{job.id}</span>

      {/* COMMAND — title + verbatim command */}
      <div className={`${CARD_COL.command} flex flex-col gap-1`}>
        <div className="flex min-w-0 items-center gap-2">
          <span className="text-text-primary truncate text-sm font-medium">{job.title}</span>
          {job.status === 'pending' && job.review_required && <NeedsReviewPill />}
        </div>
        <code
          className={`text-text-muted block truncate font-mono text-xs ${
            job.status === 'rejected' ? 'line-through' : ''
          }`}
        >
          $ {job.script_text}
        </code>
      </div>

      {/* REQUESTED BY */}
      <div className={`${CARD_COL.requestedBy} flex min-w-0 items-center gap-2`}>
        <AgentAvatar name={job.agent_name} />
        <span className="text-text-secondary truncate text-sm">{job.agent_name}</span>
      </div>

      {/* TASK */}
      <span className={`${CARD_COL.task} text-mono-sm text-accent-text truncate`}>{job.task_id}</span>

      {/* OPENED */}
      <div className={`${CARD_COL.opened} flex justify-end`}>
        <OutcomeCell job={job} />
      </div>
    </Link>
  );
}

/** Column-header row above the cards — aligns to the shared card row. */
function ColumnHeader(): JSX.Element {
  return (
    <div className={`${CARD_ROW} text-text-muted px-5 pb-1 text-xs font-medium tracking-wider uppercase`}>
      <span className={CARD_COL.job}>Job</span>
      <span className={CARD_COL.command}>Command</span>
      <span className={CARD_COL.requestedBy}>Requested by</span>
      <span className={CARD_COL.task}>Task</span>
      <span className={`${CARD_COL.opened} text-right`}>Opened</span>
    </div>
  );
}

/**
 * The amber "needs you" callout — the running total of founder-blocking
 * (pending) decisions. Collapses to a calm queue-clear line when nothing is
 * pending. Count is derived from existing data (no new field).
 */
function NeedsYouCallout({ pendingCount }: { pendingCount: number }): JSX.Element {
  if (pendingCount === 0) {
    return (
      <div className="text-accent-text flex items-center gap-2 text-sm">
        <span aria-hidden="true" className="bg-accent-default inline-block h-2 w-2 rounded-full" />
        Queue clear · nothing waiting on you
      </div>
    );
  }
  return (
    <div className="bg-attention-soft text-attention-text flex items-start gap-3 rounded-lg px-4 py-3">
      <Lock aria-hidden="true" className="mt-0.5 h-4 w-4 shrink-0" />
      <div className="min-w-0">
        <p className="text-sm font-semibold">
          {pendingCount} {pendingCount === 1 ? 'job needs' : 'jobs need'} you
        </p>
        <p className="text-sm opacity-90">
          Every job is a verbatim command an agent can&apos;t run without your sign-off. Same
          two-step confirm for all — no risk tiers.
        </p>
      </div>
    </div>
  );
}

// ── Page ─────────────────────────────────────────────────────────────

export function JobsPage(): JSX.Element {
  const { slug } = useParams<{ slug: string }>();
  // status:'all' is REQUIRED — GET /jobs/ defaults to status=pending, which
  // would hide every non-pending job and empty out the status groups.
  const query = useJobsList({ status: 'all', limit: 200 });

  const jobs = useMemo<JobRecord[]>(() => query.data?.jobs ?? [], [query.data]);
  const pendingCount = useMemo(() => jobs.filter((j) => j.status === 'pending').length, [jobs]);

  return (
    <div className="bg-surface-canvas h-full">
      <ContentWrap>
        <header className="mb-5">
          <p className="text-text-muted text-xs font-medium tracking-wide uppercase">
            Founder-gated commands · agents propose, you approve
          </p>
          <h1 className="font-display text-text-primary mt-1 text-2xl font-medium">
            Commands awaiting a decision
          </h1>
        </header>

        {query.isLoading ? (
          <p className="text-text-muted py-12 text-center text-sm">Loading jobs…</p>
        ) : query.isError ? (
          <p className="text-text-muted py-12 text-center text-sm">Could not load jobs.</p>
        ) : jobs.length === 0 ? (
          <>
            <NeedsYouCallout pendingCount={0} />
            <p className="text-text-muted py-12 text-center text-sm">No jobs yet.</p>
          </>
        ) : (
          <>
            <div className="mb-5">
              <NeedsYouCallout pendingCount={pendingCount} />
            </div>
            <ColumnHeader />
            <div className="mt-2 flex flex-col gap-6">
              {STATUS_GROUP_ORDER.map((status) => {
                const rows = jobs.filter((j) => j.status === status);
                if (rows.length === 0) return null;
                return (
                  <section
                    key={status}
                    aria-label={STATUS_GROUP_META[status].label}
                    className="flex flex-col gap-2"
                  >
                    <StatusGroupHeader status={status} count={rows.length} />
                    <ul className="flex flex-col gap-2">
                      {rows.map((job) => (
                        <li key={job.id}>
                          <JobCard job={job} to={slug ? `/orgs/${slug}/jobs/${job.id}` : '#'} />
                        </li>
                      ))}
                    </ul>
                  </section>
                );
              })}
            </div>
          </>
        )}
      </ContentWrap>
    </div>
  );
}
