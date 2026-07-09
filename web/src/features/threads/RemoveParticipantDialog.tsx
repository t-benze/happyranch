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
import { ApiError } from '@/lib/api';
import { useRemoveParticipant } from '@/hooks/threads';
import { describeError } from './strings';

interface Props {
  threadId: string;
  /** Agent to remove — also drives the open state (null ⇒ closed). */
  agentName: string | null;
  onClose: () => void;
}

export function RemoveParticipantDialog({ threadId, agentName, onClose }: Props): JSX.Element {
  const remove = useRemoveParticipant(threadId);
  const [errorMsg, setErrorMsg] = useState<string | null>(null);
  const open = agentName !== null;

  useEffect(() => {
    if (!open) return;
    setErrorMsg(null);
  }, [open]);

  const submit = async () => {
    if (!agentName) return;
    setErrorMsg(null);
    try {
      await remove.mutateAsync({ agent_name: agentName });
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
          <DialogTitle>Remove participant</DialogTitle>
          <DialogDescription>
            Remove <span className="font-medium">{agentName}</span> from this thread? They will no
            longer see new messages.
          </DialogDescription>
        </DialogHeader>
        {errorMsg && <p className="text-feedback-danger text-sm">{errorMsg}</p>}
        <DialogFooter>
          <Button type="button" variant="ghost" onClick={onClose}>Cancel</Button>
          <Button type="button" onClick={submit} disabled={remove.isPending}>
            {remove.isPending ? 'Removing…' : 'Remove'}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
