/**
 * JobDetailPage — standalone contextual job detail surface (§4.13 PRD final).
 *
 * RENDER-ONLY for stored fields + DERIVE for "if-approved" cascade.
 * No standalone Jobs index — reached contextually from Audit timeline,
 * task detail, or artifact cards.
 *
 * Direction-A alignment (TASK-912, a-job-detail.html): a serif job-TITLE
 * headline with the JOB id + status as a compact eyebrow row, the action
 * buttons hoisted top-right, a "Verbatim command · runs exactly this"
 * command card, a carded "If approved" cascade, and a curated approval-
 * context right-rail (Requested by / Created lead) with the execution
 * telemetry kept present but secondary.
 *
 * Honesty fence — the design's curated `Routed via`, `Thread`, `Kind`,
 * `File` rows and its `PR #NNN` chip have NO backing JobRecord field, so they
 * are honestly omitted rather than fabricated; the back-link stays
 * "← Back to {task_id}" (there is no originating-thread field to back to).
 * The design's "Decline & revert code instead" implies a revert *behavior*
 * we do not implement (TASK-414 fence) — the secondary action keeps the
 * existing honest reject wiring (RejectJobDialog) and a "Reject" label.
 * See TASK-912 completion report for the deferred data-field follow-up list.
 *
 * Key behaviours:
 * - Verbatim command in monospace
 * - "If approved" cascade lists real tasks blocked on this job (DERIVE)
 * - Uniform two-step confirm for approve/run (NO danger tiers)
 * - Gated (review_required) chip routes to EXISTING approve/reject flow
 * - Calm/empty/loading/error states with retry
 */
import { useState, type ReactNode } from 'react';
import { Link, useParams } from 'react-router-dom';
import { EmptyState } from '@/design-system/patterns/EmptyState';
import { StatusBadge } from '@/design-system/patterns/StatusBadge';
import { IdBadge } from '@/design-system/patterns/IdBadge';
import { AgentChip } from '@/design-system/patterns/AgentChip';
import { Button } from '@/design-system/primitives/Button';
import { useJob, useRunJob, useStopJob } from '@/hooks/jobs';
// eslint-disable-next-line no-restricted-imports -- need blocked_on_job_id filter not exposed via hooks; THR-011 option 3
import { listTasks } from '@/lib/api/tasks';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import type { JobRecord, TaskRecord } from '@/lib/api/types';
import { RejectJobDialog } from './RejectJobDialog';
import { RunJobDialog } from './RunJobDialog';
import { OutputPanel } from './OutputPanel';

// ── Helpers ──────────────────────────────────────────────────────────

function formatDateTime(iso: string | null | undefined): string | null {
  if (!iso) return null;
  return new Date(iso).toLocaleString();
}

// ── Sub-components ───────────────────────────────────────────────────

/** Section eyebrow — uppercase caption shared by the command + rail groups. */
function Eyebrow({ children }: { children: ReactNode }): JSX.Element {
  return (
    <p className="text-text-secondary text-xs font-semibold tracking-wider uppercase">
      {children}
    </p>
  );
}

/**
 * Command card styled with terminal chrome per the a-job-detail Direction-A
 * reference (JOBDET-02 + TASK-912): a "›_ command" header bar above the
 * verbatim script with a leading "$" prompt glyph, and an interpreter/cwd
 * footer. Pure restyle of existing fields — interpreter and cwd_hint come
 * straight from the job payload; cwd is omitted honestly when absent (no
 * fabricated value).
 */
function ScriptBlock({ job }: { job: JobRecord }): JSX.Element {
  return (
    <section className="border-border-default overflow-hidden rounded-lg border">
      <div className="bg-surface-sunken border-border-default flex items-center gap-2 border-b px-3 py-2">
        <span aria-hidden="true" className="text-text-muted font-mono text-xs select-none">
          ›_
        </span>
        <span className="text-text-muted text-xs font-medium tracking-wider uppercase">command</span>
      </div>
      <pre className="bg-surface-canvas text-text-primary overflow-x-auto p-3 font-mono text-xs whitespace-pre">
        <span aria-hidden="true" className="text-accent-default select-none">$ </span>
        {job.script_text}
      </pre>
      <div className="bg-surface-sunken border-border-default text-text-muted border-t px-3 py-1.5 font-mono text-xs">
        {job.interpreter}{job.cwd_hint ? ` · cwd: ${job.cwd_hint}` : ''}
      </div>
    </section>
  );
}

/**
 * Role for the agent avatar chip. Only the founder is distinguishable from a
 * bare agent-name string, so every other agent renders as a worker (the dot is
 * decorative). Mirrors the Tasks/Threads surfaces' honest mapping.
 */
function chipRole(name: string): 'worker' | 'founder' {
  return name === 'founder' ? 'founder' : 'worker';
}

/** One label/value row inside the metadata rail card. */
function RailRow({ label, children }: { label: string; children: ReactNode }): JSX.Element {
  return (
    <div className="flex items-baseline gap-3">
      <dt className="text-text-muted w-24 shrink-0 text-xs">{label}</dt>
      <dd className="min-w-0 flex-1">{children}</dd>
    </div>
  );
}

/** Mono value for stored/technical fields (preserves the prior grid styling). */
function MonoValue({ children }: { children: ReactNode }): JSX.Element {
  return (
    <span className="text-text-primary font-mono text-xs break-words tabular-nums">{children}</span>
  );
}

/**
 * Metadata rail — right-rail card styled per the a-job-detail reference.
 * LEADS with the curated approval-context fields (Requested by / Created)
 * per the Direction-A design (TASK-912), with the Task entity link and any
 * reviewer alongside. The shipped execution telemetry (interpreter, cwd,
 * exit code, durations, …) is preserved below a divider as a secondary
 * "Execution" group — present, not deleted. The reference's curated
 * Routed-via / Thread / Kind / File rows have no backing value in the job
 * payload, so they are honestly omitted rather than fabricated.
 */
function PropertyRail({ job, slug }: { job: JobRecord; slug: string | undefined }): JSX.Element {
  // Secondary execution-telemetry fields, in the prior grid's order; only non-null shown.
  const telemetry: { label: string; value: string | null }[] = [
    { label: 'Interpreter', value: job.interpreter },
    { label: 'CWD hint', value: job.cwd_hint },
    { label: 'CWD resolved', value: job.cwd_resolved },
    { label: 'Started', value: formatDateTime(job.started_at) },
    { label: 'Finished', value: formatDateTime(job.finished_at) },
    { label: 'Reviewed at', value: formatDateTime(job.reviewed_at) },
    { label: 'Exit code', value: job.exit_code !== null ? String(job.exit_code) : null },
    { label: 'Duration', value: job.duration_ms !== null ? `${(job.duration_ms / 1000).toFixed(1)}s` : null },
    { label: 'Max runtime', value: job.max_runtime_seconds !== null ? `${job.max_runtime_seconds}s` : 'unbounded' },
    { label: 'Persistent', value: job.persistent ? 'yes' : 'no' },
    { label: 'Review required', value: job.review_required ? 'yes' : 'no' },
  ];

  return (
    <aside className="lg:w-72 lg:shrink-0">
      <div className="border-border-default bg-surface-raised rounded-xl border p-4">
        {/* Curated approval-context — leads the rail per Direction-A. */}
        <dl className="space-y-3 text-sm">
          <RailRow label="Requested by">
            <AgentChip name={job.agent_name} role={chipRole(job.agent_name)} />
          </RailRow>
          {job.reviewed_by && (
            <RailRow label="Reviewed by">
              <AgentChip name={job.reviewed_by} role={chipRole(job.reviewed_by)} />
            </RailRow>
          )}
          <RailRow label="Task">
            <IdBadge
              id={job.task_id}
              kind="task"
              to={slug ? `/orgs/${slug}/tasks/${job.task_id}` : undefined}
            />
          </RailRow>
          <RailRow label="Created">
            <span className="text-text-primary text-xs">{formatDateTime(job.created_at)}</span>
          </RailRow>
        </dl>

        {/* Execution telemetry — kept present, secondary to the curated context. */}
        <div className="border-border-default mt-4 border-t pt-4">
          <Eyebrow>Execution</Eyebrow>
          <dl className="mt-3 space-y-3 text-sm">
            {telemetry.map(({ label, value }) =>
              value !== null ? (
                <RailRow key={label} label={label}>
                  <MonoValue>{value}</MonoValue>
                </RailRow>
              ) : null,
            )}
          </dl>
        </div>
      </div>
    </aside>
  );
}

/** "If approved" cascade — DERIVE: lists tasks blocked on this job, carded
 *  with impact dots per the Direction-A reference. */
function IfApprovedCascade({ slug, jobId }: { slug: string; jobId: string }): JSX.Element {
  const blockedTasksQuery = useQuery({
    queryKey: ['tasks-blocked-on-job', slug, jobId],
    queryFn: () =>
      listTasks(slug, {
        blocked_on_job_id: jobId,
        limit: 50,
      }),
    enabled: !!slug,
  });

  const tasks: TaskRecord[] = (blockedTasksQuery.data?.tasks as TaskRecord[]) ?? [];

  function Card({ title, children }: { title: string; children: ReactNode }): JSX.Element {
    return (
      <section className="border-border-default bg-surface-raised rounded-xl border p-4">
        <h3 className="text-text-primary mb-3 text-sm font-semibold">{title}</h3>
        {children}
      </section>
    );
  }

  if (blockedTasksQuery.isLoading) {
    return (
      <Card title="If approved">
        <p className="text-text-muted text-sm">Loading blocked tasks…</p>
      </Card>
    );
  }

  if (blockedTasksQuery.isError) {
    return (
      <Card title="If approved">
        <p className="text-text-muted text-sm">Could not load blocked tasks.</p>
      </Card>
    );
  }

  if (tasks.length === 0) {
    return (
      <Card title="If approved">
        <p className="text-text-muted text-sm">No tasks are currently blocked on this job.</p>
      </Card>
    );
  }

  return (
    <Card title={`If approved — ${tasks.length} task${tasks.length !== 1 ? 's' : ''} unblocks`}>
      <ul className="space-y-2">
        {tasks.map((t) => (
          <li key={t.task_id} className="flex items-center gap-2.5 text-sm">
            <span
              aria-hidden="true"
              className="bg-accent-default h-2 w-2 shrink-0 rounded-full"
            />
            <Link
              to={`/orgs/${slug}/tasks/${t.task_id}`}
              className="text-accent-default font-mono text-xs hover:underline"
            >
              {t.task_id}
            </Link>
            <span className="text-text-primary min-w-0 truncate">
              {t.brief.slice(0, 80)}{t.brief.length > 80 ? '…' : ''}
            </span>
            <StatusBadge
              status={t.status as 'blocked'}
              blockKind={(t as { block_kind?: string }).block_kind as 'escalated' | 'delegated' | null}
            />
          </li>
        ))}
      </ul>
    </Card>
  );
}

/** Uniform two-step confirm for approve/run — no danger tiers. */
function TwoStepConfirm({
  action,
  onConfirm,
  onCancel,
  isPending,
}: {
  action: 'approve' | 'run';
  onConfirm: () => void;
  onCancel: () => void;
  isPending: boolean;
}): JSX.Element {
  const [step, setStep] = useState<1 | 2>(1);

  return (
    <div className="border-border-default bg-surface-raised shadow-pasture-sm mt-4 rounded-lg border p-4">
      {step === 1 ? (
        <div className="flex items-center gap-3">
          <p className="text-text-primary text-sm">
            {action === 'approve'
              ? 'Approve this job to allow the blocked tasks to proceed.'
              : 'Run this script. It will execute immediately in the agent workspace.'}
          </p>
          <div className="flex shrink-0 gap-2">
            <Button variant="ghost" size="sm" onClick={onCancel}>
              Cancel
            </Button>
            <Button size="sm" onClick={() => setStep(2)}>
              {action === 'approve' ? 'Approve…' : 'Run…'}
            </Button>
          </div>
        </div>
      ) : (
        <div className="space-y-3">
          <p className="text-attention-text text-sm font-medium">
            Confirm: are you sure you want to {action} this job?
          </p>
          <div className="flex gap-2">
            <Button variant="ghost" size="sm" onClick={() => setStep(1)}>
              Back
            </Button>
            <Button
              size="sm"
              onClick={onConfirm}
              disabled={isPending}
            >
              {isPending ? 'Running…' : `Confirm ${action}`}
            </Button>
            <Button variant="ghost" size="sm" onClick={onCancel}>
              Cancel
            </Button>
          </div>
        </div>
      )}
    </div>
  );
}

/** Gated explanation card: a review_required job is approved/rejected from the
 *  top-right action buttons. Copy aligned to the Direction-A approve-card; the
 *  Approve/Reject controls live in the header and route to the EXISTING
 *  two-step run confirm + RejectJobDialog. */
function GatedNotice(): JSX.Element {
  return (
    <div className="bg-attention-soft mt-5 rounded-xl p-4">
      <div className="mb-2 flex items-center gap-2">
        <span className="bg-attention text-attention-text inline-flex items-center gap-1.5 rounded-full px-2 py-0.5 text-xs font-medium">
          <span aria-hidden="true" className="h-1.5 w-1.5 rounded-full bg-current" />
          flagged for review
        </span>
      </div>
      <p className="text-attention-text text-sm font-semibold">
        This action is gated — confirm from the buttons above.
      </p>
      <p className="text-attention-text/85 mt-1 text-xs leading-relaxed">
        Every gated action uses the same two-step confirm — no risk tiers. The
        assistant can propose this; only you approve it.
      </p>
    </div>
  );
}

// ── Main page ────────────────────────────────────────────────────────

type OpenDialog = 'reject' | 'run' | null;

export function JobDetailPage(): JSX.Element {
  const { slug, job_id: jobId } = useParams<{ slug: string; job_id: string }>();
  const query = useJob(jobId);
  const qc = useQueryClient();
  const run = useRunJob();
  const stop = useStopJob();
  const [openDialog, setOpenDialog] = useState<OpenDialog>(null);
  const [showTwoStep, setShowTwoStep] = useState<'run' | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);

  const job = query.data;

  // Stop handler
  const onStop = async () => {
    setActionError(null);
    try {
      await stop.mutateAsync({ jobId: jobId ?? '' });
    } catch (err) {
      setActionError(err instanceof Error ? err.message : String(err));
    }
  };

  const handleRetry = () => {
    if (slug) qc.invalidateQueries({ queryKey: ['job', slug, jobId] });
  };

  // ── Loading ──
  if (query.isLoading) {
    return (
      <div className="flex h-full items-center justify-center">
        <p className="text-text-muted">Loading {jobId}…</p>
      </div>
    );
  }

  // ── Error ──
  if (query.isError) {
    return (
      <div className="flex h-full flex-col items-center justify-center gap-3 p-8">
        <p className="text-text-muted">Failed to load {jobId}.</p>
        <Button variant="ghost" size="sm" onClick={handleRetry}>
          Retry
        </Button>
      </div>
    );
  }

  // ── Not found ──
  if (!job) {
    return (
      <EmptyState
        title="Job not found"
        body={`Job ${jobId ?? 'unknown'} does not exist or was removed.`}
      />
    );
  }

  // ── Header actions — top-right per Direction-A. Relabel + relayout only;
  // every handler is the EXISTING approve/reject/run/stop wiring. Hidden while
  // the two-step confirm card is open (it carries its own controls). ──
  let headerActions: ReactNode = null;
  if (job.status === 'running') {
    headerActions = (
      <Button variant="destructive" size="sm" onClick={onStop} disabled={stop.isPending}>
        {stop.isPending ? 'Stopping…' : 'Stop'}
      </Button>
    );
  } else if (job.status === 'pending' && showTwoStep === null) {
    headerActions = (
      <>
        <Button variant="secondary" size="sm" onClick={() => setOpenDialog('reject')}>
          Reject
        </Button>
        <Button size="sm" onClick={() => setShowTwoStep('run')}>
          {job.review_required ? 'Approve job' : 'Run'}
        </Button>
      </>
    );
  }

  // ── Normal render ──
  return (
    <div className="mx-auto max-w-5xl px-4 py-6">
      {/* Breadcrumb: contextual back-link to spawning task (honesty fence — no
          originating-thread field, so we back to the task, not a thread). */}
      <nav className="mb-4">
        <Link
          to={`/orgs/${slug}/tasks/${job.task_id}`}
          className="text-text-muted hover:text-text-primary text-xs transition-colors"
        >
          ← Back to {job.task_id}
        </Link>
      </nav>

      {/* Header — JOB id + status eyebrow row with actions top-right, then the
          serif job-TITLE headline (Direction-A). */}
      <header className="mb-6">
        <div className="flex items-start justify-between gap-3">
          <span className="flex items-center gap-2">
            <span className="text-text-primary font-mono text-sm tabular-nums">{job.id}</span>
            <StatusBadge status={job.status as 'pending'} />
          </span>
          {headerActions && (
            <div className="flex shrink-0 items-center gap-2">{headerActions}</div>
          )}
        </div>
        <h1 className="font-display text-h1 text-text-primary mt-3 font-medium">{job.title}</h1>
      </header>

      {/* Two-column body: primary content + right-rail metadata card */}
      <div className="flex flex-col gap-6 lg:flex-row lg:items-start">
        <div className="min-w-0 flex-1 space-y-5">
          {/* Rationale */}
          {job.rationale && (
            <p className="text-text-primary text-sm leading-relaxed whitespace-pre-wrap">
              {job.rationale}
            </p>
          )}

          {/* Verbatim command */}
          <div>
            <Eyebrow>Verbatim command · runs exactly this</Eyebrow>
            <div className="mt-3">
              <ScriptBlock job={job} />
            </div>
            <p className="text-text-muted mt-2.5 text-xs leading-relaxed">
              No diff is stored — what you approve is the exact command above. Its
              effect is the downstream cascade below.
            </p>
          </div>

          {/* Rejection reason */}
          {job.status === 'rejected' && job.reject_reason && (
            <section>
              <Eyebrow>Rejection reason</Eyebrow>
              <p className="text-text-primary mt-2 text-sm whitespace-pre-wrap">{job.reject_reason}</p>
            </section>
          )}

          {/* Failure reason */}
          {job.status === 'failed' && job.reason && (
            <section>
              <Eyebrow>Failure reason</Eyebrow>
              <p className="text-text-primary mt-2 font-mono text-sm">{job.reason}</p>
            </section>
          )}

          {/* "If approved" cascade — always visible for pending jobs */}
          {job.status === 'pending' && slug && (
            <IfApprovedCascade slug={slug} jobId={jobId ?? ''} />
          )}

          {/* Gated (review_required) pending job → explanation card; the
              Approve/Reject controls live in the header above. */}
          {job.status === 'pending' && job.review_required && showTwoStep === null && (
            <GatedNotice />
          )}

          {/* Two-step confirm for run (used by both gated and non-gated paths) */}
          {job.status === 'pending' && showTwoStep === 'run' && (
            <TwoStepConfirm
              action="run"
              onConfirm={() => {
                setShowTwoStep(null);
                setOpenDialog('run');
              }}
              onCancel={() => setShowTwoStep(null)}
              isPending={run.isPending}
            />
          )}

          {/* Running job: stop error feedback (Stop button is in the header) */}
          {job.status === 'running' && actionError && (
            <p className="text-feedback-danger text-sm">{actionError}</p>
          )}

          {/* Output panel */}
          <OutputPanel job={job} slug={slug ?? ''} />
        </div>

        {/* Right-rail metadata card */}
        <PropertyRail job={job} slug={slug} />
      </div>

      {/* ── Dialogs ── */}
      {openDialog === 'reject' && (
        <RejectJobDialog
          jobId={jobId ?? ''}
          open
          onClose={() => setOpenDialog(null)}
        />
      )}
      {openDialog === 'run' && (
        <RunJobDialog
          job={job}
          open
          onClose={() => setOpenDialog(null)}
        />
      )}
    </div>
  );
}
