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
import { useOrgSlug } from '@/lib/orgSlug';
import { useArchiveThread } from './hooks';
import { describeError } from './strings';

interface Props {
  threadId: string;
  open: boolean;
  onClose: () => void;
}

export function ArchiveDialog({ threadId, open, onClose }: Props): JSX.Element {
  const archive = useArchiveThread(useOrgSlug(), threadId);
  const [summary, setSummary] = useState('');
  const [requestCloseOuts, setRequestCloseOuts] = useState(true);
  const [errorMsg, setErrorMsg] = useState<string | null>(null);
  const summaryId = useId();
  const closeOutsId = useId();

  useEffect(() => {
    if (!open) return;
    setSummary('');
    setRequestCloseOuts(true);
    setErrorMsg(null);
  }, [open]);

  const submit = async () => {
    setErrorMsg(null);
    if (!summary.trim()) {
      setErrorMsg('Summary is required.');
      return;
    }
    try {
      await archive.mutateAsync({
        summary: summary.trim(),
        request_close_outs: requestCloseOuts,
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
            Archive this thread with a founder summary; optionally request close-outs from participants.
          </DialogDescription>
        </DialogHeader>
        <div className="flex flex-col gap-3">
          <FormField
            label="Founder summary (will be saved to transcript)"
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
          <label
            htmlFor={closeOutsId}
            className="flex items-center gap-2 text-xs text-text-primary"
          >
            <input
              id={closeOutsId}
              type="checkbox"
              checked={requestCloseOuts}
              onChange={(e) => setRequestCloseOuts(e.target.checked)}
            />
            Request close-outs from each participant
          </label>
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
