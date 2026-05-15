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
import { useAbandonThread } from '@/hooks/threads';
import { describeError } from './strings';

interface Props {
  threadId: string;
  open: boolean;
  onClose: () => void;
}

export function AbandonDialog({ threadId, open, onClose }: Props): JSX.Element {
  const abandon = useAbandonThread(threadId);
  const [reason, setReason] = useState('');
  const [errorMsg, setErrorMsg] = useState<string | null>(null);
  const reasonId = useId();

  useEffect(() => {
    if (!open) return;
    setReason('');
    setErrorMsg(null);
  }, [open]);

  const submit = async () => {
    setErrorMsg(null);
    if (!reason.trim()) {
      setErrorMsg('Reason is required.');
      return;
    }
    try {
      await abandon.mutateAsync({ reason: reason.trim() });
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
          <DialogTitle>Abandon thread</DialogTitle>
          <DialogDescription className="sr-only">
            Permanently abandon this thread. No close-outs requested.
          </DialogDescription>
        </DialogHeader>
        <div className="flex flex-col gap-3">
          <p className="text-xs text-text-muted">
            Abandons the thread without close-outs. Use this when the thread is no longer useful.
          </p>
          <FormField label="Reason" htmlFor={reasonId} error={errorMsg ?? undefined}>
            <input
              id={reasonId}
              type="text"
              value={reason}
              onChange={(e) => setReason(e.target.value)}
              autoFocus
              className="input"
              onKeyDown={(e) => {
                if (e.key === 'Enter') {
                  e.preventDefault();
                  submit();
                }
              }}
            />
          </FormField>
        </div>
        <DialogFooter>
          <Button variant="ghost" onClick={onClose}>Cancel</Button>
          <Button variant="destructive" onClick={submit} disabled={abandon.isPending}>
            {abandon.isPending ? 'Abandoning…' : 'Abandon'}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
