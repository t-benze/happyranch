import { useEffect, useId, useState } from 'react';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/design-system/primitives/Dialog';
import { Button } from '@/design-system/primitives/Button';
import { FormField } from '@/design-system/patterns/FormField';
import { Input } from '@/design-system/primitives/Input';
import { useRunJob } from '@/hooks/jobs';
import { useTranslation } from '@/hooks/i18n';
import type { JobRecord } from '@/lib/api/types';
import { classifyJobError, renderJobError, type JobErrorView } from './strings';

interface Props {
  job: JobRecord;
  open: boolean;
  onClose: () => void;
  onSuccess?: () => void;
}

// Initial value for the timeout field. For persistent jobs the daemon
// accepts no cap; we expose the field as blank so the founder can leave
// it unset (sent as no override). For bounded jobs we seed the field
// with the job's declared cap, falling back to 300s (the daemon's default
// for non-persistent jobs when no explicit cap is provided).
function initialTimeout(job: JobRecord): string {
  if (job.max_runtime_seconds !== null && job.max_runtime_seconds !== undefined) {
    return String(job.max_runtime_seconds);
  }
  return job.persistent ? '' : '300';
}

export function RunJobDialog({ job, open, onClose, onSuccess }: Props): JSX.Element {
  const { t } = useTranslation();
  const run = useRunJob();
  const [cwdOverride, setCwdOverride] = useState('');
  const [timeoutSecondsInput, setTimeoutSecondsInput] = useState(initialTimeout(job));
  const [error, setError] = useState<JobErrorView | null>(null);
  const cwdId = useId();
  const timeoutId = useId();

  useEffect(() => {
    if (!open) return;
    setCwdOverride('');
    setTimeoutSecondsInput(initialTimeout(job));
    setError(null);
  }, [open, job]);

  const parsedTimeout = timeoutSecondsInput.trim() === ''
    ? null
    : Number(timeoutSecondsInput);
  const timeoutInvalid =
    parsedTimeout !== null && (!Number.isFinite(parsedTimeout) || parsedTimeout < 1);

  const submit = async () => {
    setError(null);
    const body: { cwd_override?: string; timeout_seconds?: number } = {};
    if (cwdOverride.trim()) body.cwd_override = cwdOverride.trim();
    if (parsedTimeout !== null && parsedTimeout !== job.max_runtime_seconds) {
      body.timeout_seconds = parsedTimeout;
    }
    try {
      await run.mutateAsync({ jobId: job.id, body });
      onSuccess?.();
      onClose();
    } catch (err) {
      setError(classifyJobError(err, 'jobs.run.failed'));
    }
  };

  return (
    <Dialog open={open} onOpenChange={(o) => { if (!o) onClose(); }}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle className="font-display">
            {job.review_required
              ? t('jobs.run.titleApprove', { jobId: job.id })
              : t('jobs.run.titleRun', { jobId: job.id })}
          </DialogTitle>
          <DialogDescription className="sr-only">
            {t('jobs.run.description')}
          </DialogDescription>
        </DialogHeader>

        {/* Script preview */}
        <div className="min-w-0 space-y-3">
          <div className="min-w-0">
            <p className="text-text-muted mb-1 text-xs font-medium tracking-wider uppercase">
              {t('jobs.run.script')}
              <span className="ml-1 normal-case">
                {job.cwd_hint
                  ? t('jobs.run.scriptMetaCwd', { interpreter: job.interpreter, cwd: job.cwd_hint })
                  : t('jobs.run.scriptMeta', { interpreter: job.interpreter })}
              </span>
            </p>
            <pre className="bg-surface-sunken border-border-default text-text-primary max-h-40 max-w-full min-w-0 overflow-x-auto overflow-y-auto rounded-lg border p-3 text-xs whitespace-pre">
              {job.script_text}
            </pre>
          </div>

          <FormField label={t('jobs.run.cwdOverride')} htmlFor={cwdId}>
            <Input
              id={cwdId}
              type="text"
              placeholder={job.cwd_hint ?? t('jobs.run.cwdPlaceholder')}
              value={cwdOverride}
              onChange={(e) => setCwdOverride(e.target.value)}
            />
          </FormField>

          <FormField
            label={job.persistent ? t('jobs.run.timeoutUnbounded') : t('jobs.run.timeout')}
            htmlFor={timeoutId}
          >
            <Input
              id={timeoutId}
              type="number"
              min={1}
              value={timeoutSecondsInput}
              onChange={(e) => setTimeoutSecondsInput(e.target.value)}
            />
          </FormField>

          {error !== null && (
            <p className="text-feedback-danger text-sm">{renderJobError(error, t)}</p>
          )}
        </div>

        <DialogFooter>
          <Button variant="ghost" onClick={onClose}>
            {t('common.cancel')}
          </Button>
          <Button
            variant="default"
            onClick={submit}
            disabled={run.isPending || timeoutInvalid}
          >
            {run.isPending
              ? t('jobs.run.running')
              : job.review_required
                ? t('jobs.action.approveRun')
                : t('jobs.action.run')}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
