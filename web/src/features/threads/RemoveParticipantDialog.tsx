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
  /** Participant to remove. `null` keeps the dialog closed. */
  agentName: string | null;
  open: boolean;
  onClose: () => void;
}

/**
 * Confirm-then-remove dialog — the destructive mirror of InviteDialog. The
 * dialog itself is the confirm step: it names the participant and requires an
 * explicit Remove click before firing removeParticipantFromThread.
 */
export function RemoveParticipantDialog({ threadId, agentName, open, onClose }: Props): JSX.Element {
  const remove = useRemoveParticipant(threadId);
  const [errorMsg, setErrorMsg] = useState<string | null>(null);

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
            Remove <span className="font-semibold">{agentName}</span> from this thread? They will
            stop receiving messages and any pending replies are cancelled.
          </DialogDescription>
        </DialogHeader>
        {errorMsg && <p className="text-feedback-danger text-body">{errorMsg}</p>}
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
