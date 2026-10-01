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
import { useArchiveThread } from '@/hooks/threads';
import { useTranslation } from '@/hooks/i18n';
import { classifyThreadError, renderThreadError, type ThreadErrorView } from '@/lib/threadErrors';

interface Props {
  threadId: string;
  open: boolean;
  onClose: () => void;
}

export function ArchiveDialog({ threadId, open, onClose }: Props): JSX.Element {
  const { t } = useTranslation();
  const archive = useArchiveThread(threadId);
  const [summary, setSummary] = useState('');
  // Locale-neutral error descriptor, rendered through `t` on every render so a
  // mapped message re-translates in place and a raw diagnostic stays byte-exact.
  const [errorView, setErrorView] = useState<ThreadErrorView | null>(null);
  const summaryId = useId();

  useEffect(() => {
    if (!open) return;
    setSummary('');
    setErrorView(null);
  }, [open]);

  const submit = async () => {
    setErrorView(null);
    try {
      await archive.mutateAsync({
        summary: summary.trim(),
      });
      onClose();
    } catch (err) {
      setErrorView({ detail: classifyThreadError(err) });
    }
  };

  return (
    <Dialog open={open} onOpenChange={(o) => { if (!o) onClose(); }}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>{t('threads.dialog.archive.title')}</DialogTitle>
          <DialogDescription className="sr-only">
            {t('threads.dialog.archive.description')}
          </DialogDescription>
        </DialogHeader>
        <div className="flex flex-col gap-3">
          <FormField
            label={t('threads.dialog.archive.summaryLabel')}
            htmlFor={summaryId}
          >
            <textarea
              id={summaryId}
              value={summary}
              onChange={(e) => setSummary(e.target.value)}
              rows={5}
              autoFocus
              className="input resize-y"
            />
            {/* Same node/classes FormField renders for `error`, but gated on the
                descriptor (not text truthiness) so an empty raw diagnostic
                still renders byte-for-byte. */}
            {errorView !== null && (
              <p className="text-caption text-feedback-danger" role="alert">
                {renderThreadError(errorView, t)}
              </p>
            )}
          </FormField>
        </div>
        <DialogFooter>
          <Button variant="ghost" onClick={onClose}>{t('common.cancel')}</Button>
          <Button onClick={submit} disabled={archive.isPending}>
            {archive.isPending ? t('threads.dialog.archive.pending') : t('threads.dialog.archive.confirm')}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
