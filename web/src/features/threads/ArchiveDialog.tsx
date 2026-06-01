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
import { ApiError } from '@/lib/api';
import { useArchiveThread } from '@/hooks/threads';
import { describeError } from './strings';

interface Props {
  threadId: string;
  open: boolean;
  onClose: () => void;
}

export function ArchiveDialog({ threadId, open, onClose }: Props): JSX.Element {
  const archive = useArchiveThread(threadId);
  const [summary, setSummary] = useState('');
  const [errorMsg, setErrorMsg] = useState<string | null>(null);
  const summaryId = useId();

  useEffect(() => {
    if (!open) return;
    setSummary('');
    setErrorMsg(null);
  }, [open]);

  const submit = async () => {
    setErrorMsg(null);
    try {
      await archive.mutateAsync({
        summary: summary.trim(),
      });
      onClose();
    } catch (err) {
      setErrorMsg(
        err instanceof ApiError ? describeError(err.code, `HTTP ${err.status}`) : String(err),
      );
    }
  };

  return (
    <Dialog open={open} onOpenChange={(o) => { if (!o) onClose(); }}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Archive thread</DialogTitle>
          <DialogDescription className="sr-only">
            Archive this thread. A summary will be saved to the transcript.
          </DialogDescription>
        </DialogHeader>
        <div className="flex flex-col gap-3">
          <FormField
            label="Founder summary (optional, will be saved to transcript)"
            htmlFor={summaryId}
            error={errorMsg ?? undefined}
          >
            <textarea
              id={summaryId}
              value={summary}
              onChange={(e) => setSummary(e.target.value)}
              rows={5}
              autoFocus
              className="input resize-y"
            />
          </FormField>
        </div>
        <DialogFooter>
          <Button variant="ghost" onClick={onClose}>Cancel</Button>
          <Button onClick={submit} disabled={archive.isPending}>
            {archive.isPending ? 'Archiving…' : 'Archive'}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
