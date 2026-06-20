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
import { Textarea } from '@/design-system/primitives/Textarea';
import { ApiError } from '@/lib/api';
import { useRejectJob } from '@/hooks/jobs';

interface Props {
  jobId: string;
  open: boolean;
  onClose: () => void;
  onSuccess?: () => void;
}

export function RejectJobDialog({ jobId, open, onClose, onSuccess }: Props): JSX.Element {
  const reject = useRejectJob();
  const [reason, setReason] = useState('');
  const [errorMsg, setErrorMsg] = useState<string | null>(null);
  const reasonId = useId();

  useEffect(() => {
    if (!open) return;
    setReason('');
    setErrorMsg(null);
  }, [open]);

  const canSubmit = reason.trim().length > 0 && reason.trim().length <= 1000;

  const submit = async () => {
    setErrorMsg(null);
    if (!reason.trim()) {
      setErrorMsg('Reason is required.');
      return;
    }
    if (reason.trim().length > 1000) {
      setErrorMsg('Reason must be 1000 characters or fewer.');
      return;
    }
    try {
      await reject.mutateAsync({ jobId, body: { reason: reason.trim() } });
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
          <DialogTitle className="font-display">Reject {jobId}</DialogTitle>
          <DialogDescription className="sr-only">
            Reject this job. The requesting agent will be notified.
          </DialogDescription>
        </DialogHeader>
        <div className="flex flex-col gap-3">
          <FormField label="Reason" htmlFor={reasonId} error={errorMsg ?? undefined}>
            <Textarea
              id={reasonId}
              rows={5}
              placeholder="Reason (required, max 1000 chars)"
              value={reason}
              onChange={(e) => setReason(e.target.value)}
              autoFocus
            />
          </FormField>
          <p className="text-text-muted text-right font-mono text-xs tabular-nums">
            {reason.length}/1000
          </p>
        </div>
        <DialogFooter>
          <Button variant="ghost" onClick={onClose}>Cancel</Button>
          <Button
            variant="destructive"
            onClick={submit}
            disabled={!canSubmit || reject.isPending}
          >
            {reject.isPending ? 'Rejecting…' : 'Reject'}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
