import { useEffect, useState } from 'react';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/design-system/primitives/Dialog';
import { Button } from '@/design-system/primitives/Button';
import { Textarea } from '@/design-system/primitives/Textarea';
import { ApiError } from '@/lib/api';
import { useAbandonTalk } from '@/hooks/talks';
import { describeTalksError } from './strings';

interface Props {
  talkId: string;
  open: boolean;
  onClose: () => void;
}

export function AbandonTalkDialog({ talkId, open, onClose }: Props): JSX.Element {
  const abandon = useAbandonTalk(talkId);
  const [reason, setReason] = useState('');
  const [errorMsg, setErrorMsg] = useState<string | null>(null);

  useEffect(() => {
    if (!open) {
      setReason('');
      setErrorMsg(null);
    }
  }, [open]);

  const submit = async () => {
    setErrorMsg(null);
    try {
      await abandon.mutateAsync({ reason: reason.trim() });
      onClose();
    } catch (err) {
      setErrorMsg(
        err instanceof ApiError
          ? describeTalksError(err.code, `HTTP ${err.status}`)
          : String(err),
      );
    }
  };

  return (
    <Dialog open={open} onOpenChange={(o) => { if (!o) onClose(); }}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Abandon talk {talkId}</DialogTitle>
          <DialogDescription className="text-fg-muted text-sm">
            Closes the talk without recording a transcript or learnings.
          </DialogDescription>
        </DialogHeader>
        <Textarea
          value={reason}
          onChange={(e) => setReason(e.target.value)}
          rows={3}
          placeholder="Reason"
          aria-label="Reason"
        />
        {errorMsg && <p className="text-danger text-sm">{errorMsg}</p>}
        <DialogFooter>
          <Button variant="ghost" onClick={onClose}>Back</Button>
          <Button
            variant="destructive"
            disabled={!reason.trim() || abandon.isPending}
            onClick={submit}
          >
            {abandon.isPending ? 'Abandoning…' : 'Confirm abandon'}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
