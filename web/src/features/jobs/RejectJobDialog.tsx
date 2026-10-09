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
import { useRejectJob } from '@/hooks/jobs';
import { useTranslation } from '@/hooks/i18n';
import { classifyJobError, renderJobError, type JobErrorView } from './strings';

interface Props {
  jobId: string;
  open: boolean;
  onClose: () => void;
  onSuccess?: () => void;
}

export function RejectJobDialog({ jobId, open, onClose, onSuccess }: Props): JSX.Element {
  const { t } = useTranslation();
  const reject = useRejectJob();
  const [reason, setReason] = useState('');
  const [error, setError] = useState<JobErrorView | null>(null);
  const reasonId = useId();

  useEffect(() => {
    if (!open) return;
    setReason('');
    setError(null);
  }, [open]);

  const canSubmit = reason.trim().length > 0 && reason.trim().length <= 1000;

  const submit = async () => {
    setError(null);
    if (!reason.trim()) {
      setError({ kind: 'message', key: 'jobs.reject.required' });
      return;
    }
    if (reason.trim().length > 1000) {
      setError({ kind: 'message', key: 'jobs.reject.tooLong' });
      return;
    }
    try {
      await reject.mutateAsync({ jobId, body: { reason: reason.trim() } });
      onSuccess?.();
      onClose();
    } catch (err) {
      setError(classifyJobError(err, 'jobs.reject.failed'));
    }
  };

  return (
    <Dialog open={open} onOpenChange={(o) => { if (!o) onClose(); }}>
      <DialogContent closeLabel={t('common.close')}>
        <DialogHeader>
          <DialogTitle className="font-display">{t('jobs.reject.title', { jobId })}</DialogTitle>
          <DialogDescription className="sr-only">
            {t('jobs.reject.description')}
          </DialogDescription>
        </DialogHeader>
        <div className="flex flex-col gap-3">
          <FormField
            label={t('jobs.reject.reason')}
            htmlFor={reasonId}
            error={error !== null ? renderJobError(error, t) : undefined}
          >
            <Textarea
              id={reasonId}
              rows={5}
              placeholder={t('jobs.reject.placeholder')}
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
          <Button variant="ghost" onClick={onClose}>{t('common.cancel')}</Button>
          <Button
            variant="destructive"
            onClick={submit}
            disabled={!canSubmit || reject.isPending}
          >
            {reject.isPending ? t('jobs.reject.pending') : t('jobs.action.reject')}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
