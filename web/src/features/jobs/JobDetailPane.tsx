import { IdentityName } from '@/shared/identities/IdentityName';
import { useTranslation } from '@/hooks/i18n';
import { useState } from 'react';
import { Link, useNavigate, useParams } from 'react-router-dom';
import {
  Drawer,
  DrawerContent,
  DrawerTitle,
} from '@/design-system/primitives/Drawer';
import { Button } from '@/design-system/primitives/Button';
import { classifyJobError, renderJobError, type JobErrorView } from './strings';
import { useJob, useJobsRoutes, useStopJob } from '@/hooks/jobs';
import { cn } from '@/lib/utils';
import type { JobStatus } from '@/lib/api/types';
import { RejectJobDialog } from './RejectJobDialog';
import { OutputPanel } from './OutputPanel';

// Mirrors STATUS_CLASS from JobsPage — keep in sync until a shared
// JobStatusBadge pattern is extracted to design-system.
const STATUS_CLASS: Record<JobStatus, string> = {
  pending: 'bg-tier-yellow-tint text-status-archiving',
  running: 'bg-tier-green-tint text-status-open',
  completed: 'border border-border-default bg-transparent text-status-archived',
  failed: 'bg-tier-red-tint text-status-abandoned',
  rejected: 'border border-border-default bg-transparent text-text-muted',
};

function JobStatusBadge({ status }: { status: JobStatus }): JSX.Element {
  return (
    <span
      className={cn(
        'text-mono-sm inline-flex items-center rounded-full px-2 py-px font-mono text-xs font-semibold',
        STATUS_CLASS[status],
      )}
    >
      {status}
    </span>
  );
}

type OpenDialog = 'reject' | null;

interface JobDetailPaneProps {
  jobId: string;
}

export function JobDetailPane({ jobId }: JobDetailPaneProps): JSX.Element {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const routes = useJobsRoutes();
  const query = useJob(jobId);
  const [openDialog, setOpenDialog] = useState<OpenDialog>(null);
  const [stopError, setStopError] = useState<JobErrorView | null>(null);
  const stop = useStopJob();
  const { slug } = useParams<{ slug: string }>();

  const onClose = () => navigate(routes.inbox());

  const job = query.data;

  const onStop = async () => {
    setStopError(null);
    try {
      await stop.mutateAsync({ jobId });
    } catch (err) {
      setStopError(classifyJobError(err, 'jobs.stop.failed'));
    }
  };

  return (
    <>
      <Drawer open onOpenChange={(o) => !o && onClose()}>
        <DrawerContent className="flex max-w-screen flex-col">
          {/* ── Header ── */}
          <header className="border-border-default shrink-0 border-b p-4">
            <DrawerTitle className="text-text-primary font-display flex items-center gap-2 text-lg">
              <span className="text-id-task font-mono text-sm tabular-nums">{jobId}</span>
              {job && <JobStatusBadge status={job.status} />}
            </DrawerTitle>
            {job && (
              <p className="text-text-muted mt-2 flex flex-wrap items-center gap-x-2 gap-y-1 text-xs">
                <span><IdentityName canonicalId={job.agent_name} /></span>
                <span>·</span>
                {slug ? (
                  <Link
                    to={`/orgs/${slug}/tasks/${job.task_id}`}
                    className="text-id-task font-mono tabular-nums hover:underline"
                  >
                    {job.task_id}
                  </Link>
                ) : (
                  <span className="text-id-task font-mono tabular-nums">{job.task_id}</span>
                )}
                <span>·</span>
                <span>{job.interpreter}</span>
                {job.persistent && (
                  <>
                    <span>·</span>
                    <span className="tracking-wider uppercase">{t('jobs.rail.persistent')}</span>
                  </>
                )}
                {job.review_required && (
                  <>
                    <span>·</span>
                    <span className="tracking-wider uppercase">{t('jobs.rail.reviewRequired')}</span>
                  </>
                )}
                {job.created_at && (
                  <>
                    <span>·</span>
                    <span>{new Date(job.created_at).toLocaleString()}</span>
                  </>
                )}
              </p>
            )}
          </header>

          {/* ── Body ── */}
          <section className="min-h-0 flex-1 space-y-5 overflow-y-auto p-4">
            {query.isLoading && (
              <p className="text-text-muted text-sm">{t('jobs.detail.loading', { jobId })}</p>
            )}
            {query.isError && (
              <p className="text-text-muted text-sm">{t('jobs.detail.loadError', { jobId })}</p>
            )}
            {job && (
              <>
                {/* 1. Title */}
                <h2 className="text-text-primary font-display text-base font-semibold">{job.title}</h2>

                {/* 2. Rationale */}
                <div>
                  <h3 className="text-text-muted mb-2 text-xs font-medium tracking-wider uppercase">
                    Rationale
                  </h3>
                  <p className="text-text-primary text-sm whitespace-pre-wrap">{job.rationale}</p>
                </div>

                {/* 3. Script preview */}
                <div>
                  <h3 className="text-text-muted mb-2 text-xs font-medium tracking-wider uppercase">
                    Script
                    <span className="ml-1 normal-case">({job.interpreter}
                      {job.cwd_hint ? ` · cwd: ${job.cwd_hint}` : ''}
                    )</span>
                  </h3>
                  <pre className="bg-surface-sunken border-border-default text-text-primary overflow-x-auto rounded-lg border p-3 text-xs whitespace-pre">
                    {job.script_text}
                  </pre>
                </div>

                {job.status === 'pending' && job.review_required && (
                  <p className="text-text-muted text-sm">{t('jobs.gated.title')}</p>
                )}

                {/* 4. Action bar — pending or running */}
                {job.status === 'pending' && (
                  <div className="flex gap-3">
                    <Button
                      variant="secondary"
                      onClick={() => setOpenDialog('reject')}
                    >
                      {t('jobs.action.reject')}
                    </Button>
                  </div>
                )}

                {job.status === 'running' && (
                  <div className="flex flex-col gap-2">
                    <div className="flex gap-3">
                      <Button
                        variant="destructive"
                        onClick={onStop}
                        disabled={stop.isPending}
                      >
                        {stop.isPending ? t('jobs.action.stopping') : t('jobs.action.stop')}
                      </Button>
                    </div>
                  </div>
                )}

                {stopError && <p className="text-feedback-danger text-sm">{renderJobError(stopError, t)}</p>}

                {/* 5. Reject reason — rejected only */}
                {job.status === 'rejected' && job.reject_reason && (
                  <div>
                    <h3 className="text-text-muted mb-2 text-xs font-medium tracking-wider uppercase">
                      Reject reason
                    </h3>
                    <p className="text-sm whitespace-pre-wrap">{job.reject_reason}</p>
                  </div>
                )}

                {/* 6. Failure reason — for failed jobs, surface why */}
                {job.status === 'failed' && job.reason && (
                  <div>
                    <h3 className="text-text-muted mb-2 text-xs font-medium tracking-wider uppercase">
                      Failure reason
                    </h3>
                    <p className="text-text-primary font-mono text-sm">{job.reason}</p>
                  </div>
                )}

                {/* 7. Output panel — running / completed / failed */}
                <OutputPanel job={job} slug={slug ?? ''} />
              </>
            )}
          </section>
        </DrawerContent>
      </Drawer>

      {/* Reject dialog — mounted outside the Drawer so z-index stacks correctly */}
      {openDialog === 'reject' && (
        <RejectJobDialog
          jobId={jobId}
          open
          onClose={() => setOpenDialog(null)}
        />
      )}

    </>
  );
}
