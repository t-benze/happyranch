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
 * - Single popup confirm for approve/run (one button → RunJobDialog → execute)
 * - Gated (review_required) chip routes to EXISTING approve/reject flow
 * - Calm/empty/loading/error states with retry
 */
import { useState, type ReactNode } from 'react';
import { Link, useParams } from 'react-router-dom';
import { EmptyState } from '@/design-system/patterns/EmptyState';
import { StatusBadge } from '@/design-system/patterns/StatusBadge';
import { IdBadge } from '@/design-system/patterns/IdBadge';
import { CurrentAgentChip as AgentChip } from '@/shared/identities/CurrentAgentChip';
import { Button } from '@/design-system/primitives/Button';
import { ContentWrap } from '@/design-system/layouts/ContentWrap/ContentWrap';
import { useJob, useStopJob } from '@/hooks/jobs';
// eslint-disable-next-line no-restricted-imports -- need blocked_on_job_id filter not exposed via hooks; THR-011 option 3
import { listTasks } from '@/lib/api/tasks';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import type { JobRecord, TaskRecord } from '@/lib/api/types';
import { useTranslation } from '@/hooks/i18n';
import type { Locale, MessageKey } from '@/lib/i18n';
import { classifyJobError, renderJobError, type JobErrorView } from './strings';
import { RejectJobDialog } from './RejectJobDialog';
import { RunJobDialog } from './RunJobDialog';
import { OutputPanel } from './OutputPanel';

// ── Helpers ──────────────────────────────────────────────────────────

function formatDateTime(iso: string | null | undefined, locale: Locale): string | null {
  if (!iso) return null;
  return new Date(iso).toLocaleString(locale);
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
  const { t } = useTranslation();
  return (
    <section className="border-border-default overflow-hidden rounded-lg border">
      <div className="bg-surface-sunken border-border-default flex items-center gap-2 border-b px-3 py-2">
        <span aria-hidden="true" className="text-text-muted font-mono text-xs select-none">
          ›_
        </span>
        <span className="text-text-muted text-xs font-medium tracking-wider uppercase">{t('jobs.detail.commandHeader')}</span>
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
  const { locale, t } = useTranslation();
  const seconds = (value: string): string => t('jobs.rail.seconds', { value });
  const yesNo = (flag: boolean): string => (flag ? t('jobs.rail.yes') : t('jobs.rail.no'));
  // Secondary execution-telemetry fields, in the prior grid's order; only non-null shown.
  // The catalog key doubles as the locale-neutral React key.
  const telemetry: { labelKey: MessageKey; value: string | null }[] = [
    { labelKey: 'jobs.rail.interpreter', value: job.interpreter },
    { labelKey: 'jobs.rail.cwdHint', value: job.cwd_hint },
    { labelKey: 'jobs.rail.cwdResolved', value: job.cwd_resolved },
    { labelKey: 'jobs.rail.started', value: formatDateTime(job.started_at, locale) },
    { labelKey: 'jobs.rail.finished', value: formatDateTime(job.finished_at, locale) },
    { labelKey: 'jobs.rail.reviewedAt', value: formatDateTime(job.reviewed_at, locale) },
    { labelKey: 'jobs.rail.exitCode', value: job.exit_code !== null ? String(job.exit_code) : null },
    {
      labelKey: 'jobs.rail.duration',
      value: job.duration_ms !== null ? seconds((job.duration_ms / 1000).toFixed(1)) : null,
    },
    {
      labelKey: 'jobs.rail.maxRuntime',
      value:
        job.max_runtime_seconds !== null
          ? seconds(String(job.max_runtime_seconds))
          : t('jobs.rail.unbounded'),
    },
    { labelKey: 'jobs.rail.persistent', value: yesNo(job.persistent) },
    { labelKey: 'jobs.rail.reviewRequired', value: yesNo(job.review_required) },
  ];

  return (
    <aside className="lg:w-72 lg:shrink-0">
      <div className="border-border-default bg-surface-raised rounded-xl border p-4">
        {/* Curated approval-context — leads the rail per Direction-A. */}
        <dl className="space-y-3 text-sm">
          <RailRow label={t('jobs.rail.requestedBy')}>
            <AgentChip name={job.agent_name} role={chipRole(job.agent_name)} />
          </RailRow>
          {job.reviewed_by && (
            <RailRow label={t('jobs.rail.reviewedBy')}>
              <AgentChip name={job.reviewed_by} role={chipRole(job.reviewed_by)} />
            </RailRow>
          )}
          <RailRow label={t('jobs.rail.task')}>
            <IdBadge
              id={job.task_id}
              kind="task"
              to={slug ? `/orgs/${slug}/tasks/${job.task_id}` : undefined}
            />
          </RailRow>
          <RailRow label={t('jobs.rail.created')}>
            <span className="text-text-primary text-xs">{formatDateTime(job.created_at, locale)}</span>
          </RailRow>
        </dl>

        {/* Execution telemetry — kept present, secondary to the curated context. */}
        <div className="border-border-default mt-4 border-t pt-4">
          <Eyebrow>{t('jobs.rail.execution')}</Eyebrow>
          <dl className="mt-3 space-y-3 text-sm">
            {telemetry.map(({ labelKey, value }) =>
              value !== null ? (
                <RailRow key={labelKey} label={t(labelKey)}>
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
  const { t } = useTranslation();
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
      <Card title={t('jobs.cascade.title')}>
        <p className="text-text-muted text-sm">{t('jobs.cascade.loading')}</p>
      </Card>
    );
  }

  if (blockedTasksQuery.isError) {
    return (
      <Card title={t('jobs.cascade.title')}>
        <p className="text-text-muted text-sm">{t('jobs.cascade.loadError')}</p>
      </Card>
    );
  }

  if (tasks.length === 0) {
    return (
      <Card title={t('jobs.cascade.title')}>
        <p className="text-text-muted text-sm">{t('jobs.cascade.empty')}</p>
      </Card>
    );
  }

  return (
    <Card title={t('jobs.cascade.titleCount', { count: tasks.length })}>
      <ul className="space-y-2">
        {tasks.map((task) => (
          <li key={task.task_id} className="flex flex-wrap items-center gap-x-2.5 gap-y-1 text-sm">
            <span
              aria-hidden="true"
              className="bg-accent-default h-2 w-2 shrink-0 rounded-full"
            />
            <Link
              to={`/orgs/${slug}/tasks/${task.task_id}`}
              className="text-accent-default shrink-0 font-mono text-xs hover:underline"
            >
              {task.task_id}
            </Link>
            <span className="text-text-primary min-w-0 truncate">
              {task.brief.slice(0, 80)}{task.brief.length > 80 ? '…' : ''}
            </span>
            <span className="flex shrink-0"><StatusBadge status={task.status} blockKind={task.block_kind} waitingLabels={{ delegated: t('tasks.waiting.subtasks'), blocked_on_job: t('tasks.waiting.jobs') }} /></span>
          </li>
        ))}
      </ul>
    </Card>
  );
}

/** Gated explanation card: a review_required job is approved/rejected from the
 *  top-right action buttons. Copy aligned to the Direction-A approve-card; the
 *  Approve & run / Reject controls live in the header and route to the EXISTING
 *  single RunJobDialog confirm + RejectJobDialog. */
function GatedNotice(): JSX.Element {
  const { t } = useTranslation();
  return (
    <div className="bg-attention-soft mt-5 rounded-xl p-4">
      <div className="mb-2 flex items-center gap-2">
        <span className="bg-attention text-attention-text inline-flex items-center gap-1.5 rounded-full px-2 py-0.5 text-xs font-medium">
          <span aria-hidden="true" className="h-1.5 w-1.5 rounded-full bg-current" />
          {t('jobs.gated.chip')}
        </span>
      </div>
      <p className="text-attention-text text-sm font-semibold">
        {t('jobs.gated.title')}
      </p>
      <p className="text-attention-text/85 mt-1 text-xs leading-relaxed">
        {t('jobs.gated.body')}
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
  const stop = useStopJob();
  const { t } = useTranslation();
  const [openDialog, setOpenDialog] = useState<OpenDialog>(null);
  const [actionError, setActionError] = useState<JobErrorView | null>(null);

  const job = query.data;

  // Stop handler
  const onStop = async () => {
    setActionError(null);
    try {
      await stop.mutateAsync({ jobId: jobId ?? '' });
    } catch (err) {
      setActionError(classifyJobError(err, 'jobs.stop.failed'));
    }
  };

  const handleRetry = () => {
    if (slug) qc.invalidateQueries({ queryKey: ['job', slug, jobId] });
  };

  // ── Loading ──
  if (query.isLoading) {
    return (
      <div className="flex h-full items-center justify-center">
        <p className="text-text-muted">{t('jobs.detail.loading', { jobId: jobId ?? '' })}</p>
      </div>
    );
  }

  // ── Error ──
  if (query.isError) {
    return (
      <div className="flex h-full flex-col items-center justify-center gap-3 p-8">
        <p className="text-text-muted">{t('jobs.detail.loadError', { jobId: jobId ?? '' })}</p>
        <Button variant="ghost" size="sm" onClick={handleRetry}>
          {t('common.retry')}
        </Button>
      </div>
    );
  }

  // ── Not found ──
  if (!job) {
    return (
      <EmptyState
        title={t('jobs.detail.notFoundTitle')}
        body={t('jobs.detail.notFoundBody', { jobId: jobId ?? t('jobs.detail.unknownId') })}
      />
    );
  }

  // ── Header actions — top-right per Direction-A. Relabel + relayout only;
  // every handler is the EXISTING approve/reject/run/stop wiring. The primary
  // pending action opens the single RunJobDialog confirm directly (one button →
  // one popup → execute); for a review_required job the founder /run IS the
  // approve+run, so the label reads "Approve & run". ──
  let headerActions: ReactNode = null;
  if (job.status === 'running') {
    headerActions = (
      <Button variant="destructive" size="sm" onClick={onStop} disabled={stop.isPending}>
        {stop.isPending ? t('jobs.action.stopping') : t('jobs.action.stop')}
      </Button>
    );
  } else if (job.status === 'pending') {
    headerActions = (
      <>
        <Button variant="secondary" size="sm" onClick={() => setOpenDialog('reject')}>
          {t('jobs.action.reject')}
        </Button>
        <Button size="sm" onClick={() => setOpenDialog('run')}>
          {job.review_required ? t('jobs.action.approveRun') : t('jobs.action.run')}
        </Button>
      </>
    );
  }

  // ── Normal render ──
  // Outer scroll container — the shared AppShell <main> is `overflow-hidden`
  // and delegates scrolling to each page, so a long brief clips (and the
  // two-step confirm control becomes unreachable) without this wrapper.
  // Mirrors TaskDetailPage and every other full page.
  return (
    <ContentWrap>
        {/* Breadcrumb: contextual back-link to spawning task (honesty fence — no
            originating-thread field, so we back to the task, not a thread). */}
        <nav className="mb-4">
          <Link
            to={`/orgs/${slug}/tasks/${job.task_id}`}
            className="text-text-muted hover:text-text-primary text-xs transition-colors"
          >
            {t('jobs.detail.back', { taskId: job.task_id })}
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
              <Eyebrow>{t('jobs.detail.commandEyebrow')}</Eyebrow>
              <div className="mt-3">
                <ScriptBlock job={job} />
              </div>
              <p className="text-text-muted mt-2.5 text-xs leading-relaxed">
                {t('jobs.detail.commandNote')}
              </p>
            </div>

            {/* Rejection reason */}
            {job.status === 'rejected' && job.reject_reason && (
              <section>
                <Eyebrow>{t('jobs.detail.rejectionReason')}</Eyebrow>
                <p className="text-text-primary mt-2 text-sm whitespace-pre-wrap">{job.reject_reason}</p>
              </section>
            )}

            {/* Failure reason */}
            {job.status === 'failed' && job.reason && (
              <section>
                <Eyebrow>{t('jobs.detail.failureReason')}</Eyebrow>
                <p className="text-text-primary mt-2 font-mono text-sm">{job.reason}</p>
              </section>
            )}

            {/* "If approved" cascade — always visible for pending jobs */}
            {job.status === 'pending' && slug && (
              <IfApprovedCascade slug={slug} jobId={jobId ?? ''} />
            )}

            {/* Gated (review_required) pending job → explanation card; the
                Approve & run / Reject controls live in the header above. */}
            {job.status === 'pending' && job.review_required && (
              <GatedNotice />
            )}

            {/* Running job: stop error feedback (Stop button is in the header) */}
            {job.status === 'running' && actionError !== null && (
              <p className="text-feedback-danger text-sm">{renderJobError(actionError, t)}</p>
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
    </ContentWrap>
  );
}
