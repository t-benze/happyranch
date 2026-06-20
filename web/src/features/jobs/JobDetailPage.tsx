/**
 * JobDetailPage — standalone contextual job detail surface (§4.13 PRD final).
 *
 * RENDER-ONLY for stored fields + DERIVE for "if-approved" cascade.
 * No standalone Jobs index — reached contextually from Audit timeline,
 * task detail, or artifact cards.
 *
 * Key behaviours:
 * - Verbatim command in monospace
 * - "If approved" cascade lists real tasks blocked on this job (DERIVE)
 * - Uniform two-step confirm for approve/run (NO danger tiers)
 * - Gated (review_required) chip routes to EXISTING approve/reject flow
 * - Calm/empty/loading/error states with retry
 */
import { useState } from 'react';
import { Link, useParams } from 'react-router-dom';
import { PageHeader } from '@/design-system/patterns/PageHeader';
import { EmptyState } from '@/design-system/patterns/EmptyState';
import { StatusBadge } from '@/design-system/patterns/StatusBadge';
import { IdBadge } from '@/design-system/patterns/IdBadge';
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

function formatDateTime(iso: string | null | undefined): string | null {
  if (!iso) return null;
  return new Date(iso).toLocaleString();
}

// ── Sub-components ───────────────────────────────────────────────────

/** Monospace script block with interpreter + cwd context. */
function ScriptBlock({ job }: { job: JobRecord }): JSX.Element {
  return (
    <section>
      <h3 className="text-text-muted mb-2 text-xs font-medium tracking-wider uppercase">
        Command
        <span className="ml-1 font-normal normal-case">
          ({job.interpreter}{job.cwd_hint ? ` · cwd: ${job.cwd_hint}` : ''})
        </span>
      </h3>
      <pre className="bg-surface-sunken border-border-default text-text-primary overflow-x-auto rounded-lg border p-3 font-mono text-xs whitespace-pre">
        {job.script_text}
      </pre>
    </section>
  );
}

/** Property rail — stored fields only, no invented columns. */
function PropertyRail({ job }: { job: JobRecord }): JSX.Element {
  const items: { label: string; value: string | null }[] = [
    { label: 'Agent', value: job.agent_name },
    { label: 'Task', value: job.task_id },
    { label: 'Interpreter', value: job.interpreter },
    { label: 'CWD hint', value: job.cwd_hint },
    { label: 'CWD resolved', value: job.cwd_resolved },
    { label: 'Created', value: formatDateTime(job.created_at) },
    { label: 'Started', value: formatDateTime(job.started_at) },
    { label: 'Finished', value: formatDateTime(job.finished_at) },
    { label: 'Reviewed by', value: job.reviewed_by },
    { label: 'Reviewed at', value: formatDateTime(job.reviewed_at) },
    { label: 'Exit code', value: job.exit_code !== null ? String(job.exit_code) : null },
    { label: 'Duration', value: job.duration_ms !== null ? `${(job.duration_ms / 1000).toFixed(1)}s` : null },
    { label: 'Max runtime', value: job.max_runtime_seconds !== null ? `${job.max_runtime_seconds}s` : 'unbounded' },
    { label: 'Persistent', value: job.persistent ? 'yes' : 'no' },
    { label: 'Review required', value: job.review_required ? 'yes' : 'no' },
  ];

  // Only show items with non-null values
  const visible: { label: string; value: string }[] = [];
  for (const item of items) {
    if (item.value !== null) visible.push({ label: item.label, value: item.value });
  }

  if (visible.length === 0) return <></>;

  return (
    <section>
      <h3 className="text-text-muted mb-2 text-xs font-medium tracking-wider uppercase">
        Details
      </h3>
      <dl className="grid grid-cols-2 gap-x-4 gap-y-1 text-sm">
        {visible.map(({ label, value }) => (
          <div key={label} className="flex gap-2">
            <dt className="text-text-muted shrink-0">{label}</dt>
            <dd className="text-text-primary truncate font-mono text-xs tabular-nums">{value}</dd>
          </div>
        ))}
      </dl>
    </section>
  );
}

/** "If approved" cascade — DERIVE: lists tasks blocked on this job. */
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

  if (blockedTasksQuery.isLoading) {
    return (
      <section>
        <h3 className="text-text-muted mb-2 text-xs font-medium tracking-wider uppercase">
          If approved
        </h3>
        <p className="text-text-muted text-sm">Loading blocked tasks…</p>
      </section>
    );
  }

  if (blockedTasksQuery.isError) {
    return (
      <section>
        <h3 className="text-text-muted mb-2 text-xs font-medium tracking-wider uppercase">
          If approved
        </h3>
        <p className="text-text-muted text-sm">Could not load blocked tasks.</p>
      </section>
    );
  }

  if (tasks.length === 0) {
    return (
      <section>
        <h3 className="text-text-muted mb-2 text-xs font-medium tracking-wider uppercase">
          If approved
        </h3>
        <p className="text-text-muted text-sm">No tasks are currently blocked on this job.</p>
      </section>
    );
  }

  return (
    <section>
      <h3 className="text-text-muted mb-2 text-xs font-medium tracking-wider uppercase">
        If approved — {tasks.length} task{tasks.length !== 1 ? 's' : ''} unblocks
      </h3>
      <ul className="space-y-1">
        {tasks.map((t) => (
          <li key={t.task_id} className="flex items-center gap-2 text-sm">
            <Link
              to={`/orgs/${slug}/tasks/${t.task_id}`}
              className="text-accent-default font-mono text-xs hover:underline"
            >
              {t.task_id}
            </Link>
            <span className="text-text-primary truncate">{t.brief.slice(0, 80)}{t.brief.length > 80 ? '…' : ''}</span>
            <StatusBadge
              status={t.status as 'blocked'}
              blockKind={(t as { block_kind?: string }).block_kind as 'escalated' | 'delegated' | null}
            />
          </li>
        ))}
      </ul>
    </section>
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

/** Gated chip: review_required job routes to existing approve/reject.
 *  Same uniform two-step confirm + dialog pair as non-gated path:
 *  Approve → run two-step confirm → RunJobDialog; Reject → RejectJobDialog. */
function GatedChip({
  onApprove,
  onReject,
}: {
  onApprove: () => void;
  onReject: () => void;
}): JSX.Element {
  return (
    <div className="border-border-default bg-attention-soft shadow-pasture-sm mt-4 rounded-lg border p-4">
      <div className="flex items-center gap-3">
        <div className="flex-1">
          <p className="text-text-primary text-sm font-medium">🔑 Needs your approval</p>
          <p className="text-text-muted mt-0.5 text-xs">
            This job was flagged for review by the requesting agent. Review the command
            and approve or reject.
          </p>
        </div>
        <div className="flex shrink-0 gap-2">
          <Button size="sm" onClick={onApprove}>
            Approve
          </Button>
          <Button size="sm" variant="secondary" onClick={onReject}>
            Reject
          </Button>
        </div>
      </div>
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

  // ── Normal render ──
  return (
    <div className="mx-auto max-w-3xl px-4 py-6">
      {/* Breadcrumb: contextual back-link to spawning task */}
      <nav className="mb-4">
        <Link
          to={`/orgs/${slug}/tasks/${job.task_id}`}
          className="text-text-muted hover:text-text-primary text-xs transition-colors"
        >
          ← Back to {job.task_id}
        </Link>
      </nav>

      {/* Header */}
      <PageHeader
        title={
          <span className="flex items-center gap-2">
            <span className="text-text-primary font-mono text-base tabular-nums">{job.id}</span>
            <StatusBadge status={job.status as 'pending'} />
          </span>
        }
        meta={
          <span className="flex flex-wrap items-center gap-x-2 gap-y-1 text-xs">
            <span>{job.agent_name}</span>
            <span>·</span>
            <IdBadge id={job.task_id} kind="task" to={`/orgs/${slug}/tasks/${job.task_id}`} />
            <span>·</span>
            <span>{relativeAge(job.created_at)}</span>
          </span>
        }
      />

      {/* Title */}
      <h2 className="text-text-primary font-display mt-4 text-base font-semibold">{job.title}</h2>

      {/* Rationale */}
      {job.rationale && (
        <section className="mt-5">
          <h3 className="text-text-muted mb-2 text-xs font-medium tracking-wider uppercase">
            Rationale
          </h3>
          <p className="text-text-primary text-sm whitespace-pre-wrap">{job.rationale}</p>
        </section>
      )}

      {/* Verbatim command */}
      <div className="mt-5">
        <ScriptBlock job={job} />
      </div>

      {/* Rejection reason */}
      {job.status === 'rejected' && job.reject_reason && (
        <section className="mt-5">
          <h3 className="text-text-muted mb-2 text-xs font-medium tracking-wider uppercase">
            Rejection reason
          </h3>
          <p className="text-text-primary text-sm whitespace-pre-wrap">{job.reject_reason}</p>
        </section>
      )}

      {/* Failure reason */}
      {job.status === 'failed' && job.reason && (
        <section className="mt-5">
          <h3 className="text-text-muted mb-2 text-xs font-medium tracking-wider uppercase">
            Failure reason
          </h3>
          <p className="text-text-primary font-mono text-sm">{job.reason}</p>
        </section>
      )}

      {/* "If approved" cascade — always visible for pending jobs */}
      {job.status === 'pending' && slug && (
        <div className="mt-5">
          <IfApprovedCascade slug={slug} jobId={jobId ?? ''} />
        </div>
      )}

      {/* Property rail */}
      <div className="mt-5">
        <PropertyRail job={job} />
      </div>

      {/* ── Actions ── */}
      {/* Gated (review_required) pending job → chip with Approve + Reject */}
      {job.status === 'pending' && job.review_required && showTwoStep === null && (
        <GatedChip
          onApprove={() => setShowTwoStep('run')}
          onReject={() => setOpenDialog('reject')}
        />
      )}

      {/* Pending, non-gated → uniform two-step confirm */}
      {job.status === 'pending' && !job.review_required && showTwoStep === null && (
        <div className="mt-4 flex gap-3">
          <Button onClick={() => setShowTwoStep('run')}>Run</Button>
          <Button variant="secondary" onClick={() => setOpenDialog('reject')}>
            Reject
          </Button>
        </div>
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

      {/* Running job: Stop button */}
      {job.status === 'running' && (
        <div className="mt-4 flex flex-col gap-2">
          <div className="flex gap-3">
            <Button
              variant="destructive"
              onClick={onStop}
              disabled={stop.isPending}
            >
              {stop.isPending ? 'Stopping…' : 'Stop'}
            </Button>
          </div>
          {actionError && (
            <p className="text-feedback-danger text-sm">{actionError}</p>
          )}
        </div>
      )}

      {/* Output panel */}
      <div className="mt-5">
        <OutputPanel job={job} slug={slug ?? ''} />
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
