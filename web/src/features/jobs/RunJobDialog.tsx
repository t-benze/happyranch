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
import { ApiError } from '@/lib/api';
import { useRunJob } from '@/hooks/jobs';
import type { JobRecord } from '@/lib/api/types';

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
  const run = useRunJob();
  const [cwdOverride, setCwdOverride] = useState('');
  const [timeoutSecondsInput, setTimeoutSecondsInput] = useState(initialTimeout(job));
  const [errorMsg, setErrorMsg] = useState<string | null>(null);
  const cwdId = useId();
  const timeoutId = useId();

  useEffect(() => {
    if (!open) return;
    setCwdOverride('');
    setTimeoutSecondsInput(initialTimeout(job));
    setErrorMsg(null);
  }, [open, job]);

  const parsedTimeout = timeoutSecondsInput.trim() === ''
    ? null
    : Number(timeoutSecondsInput);
  const timeoutInvalid =
    parsedTimeout !== null && (!Number.isFinite(parsedTimeout) || parsedTimeout < 1);

  const submit = async () => {
    setErrorMsg(null);
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
      setErrorMsg(
        err instanceof ApiError
          ? `Error ${err.status}: ${err.message}`
          : String(err),
      );
    }
  };

  return (
    <Dialog open={open} onOpenChange={(o) => { if (!o) onClose(); }}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Run {job.id}</DialogTitle>
          <DialogDescription className="sr-only">
            Approve and run this job. The script will execute immediately.
          </DialogDescription>
        </DialogHeader>

        {/* Script preview */}
        <div className="min-w-0 space-y-3">
          <div className="min-w-0">
            <p className="text-fg-muted mb-1 text-xs font-medium uppercase tracking-wider">
              Script
              <span className="ml-1 normal-case">
                ({job.interpreter}{job.cwd_hint ? ` · cwd hint: ${job.cwd_hint}` : ''})
              </span>
            </p>
            <pre className="bg-surface-canvas text-fg max-h-40 max-w-full min-w-0 overflow-x-auto overflow-y-auto rounded p-3 text-xs whitespace-pre">
              {job.script_text}
            </pre>
          </div>

          <FormField label="Working directory override" htmlFor={cwdId}>
            <Input
              id={cwdId}
              type="text"
              placeholder={job.cwd_hint ?? 'default (agent workspace)'}
              value={cwdOverride}
              onChange={(e) => setCwdOverride(e.target.value)}
            />
          </FormField>

          <FormField
            label={job.persistent ? 'Timeout (seconds, blank = unbounded)' : 'Timeout (seconds)'}
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

          {errorMsg && (
            <p className="text-fg-danger text-sm">{errorMsg}</p>
          )}
        </div>

        <DialogFooter>
          <Button variant="ghost" onClick={onClose}>
            Cancel
          </Button>
          <Button
            variant="default"
            onClick={submit}
            disabled={run.isPending || timeoutInvalid}
          >
            {run.isPending ? 'Running…' : 'Run'}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
