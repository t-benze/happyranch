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
  /** The participant to remove. When null the dialog is closed. */
  agentName: string | null;
  onClose: () => void;
}

/**
 * Confirm-then-remove dialog for a single thread participant. Mirrors the
 * invite UX (InviteDialog) and the destructive-confirm shape of ArchiveDialog:
 * an explicit confirm step guards the mutation, errors render inline, and the
 * dialog closes on success (the hook invalidates the thread detail query so the
 * participant list re-renders without the removed agent).
 */
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
            Remove <strong className="text-text-primary">{agentName}</strong> from this thread?
            They will no longer receive broadcast messages here.
          </DialogDescription>
        </DialogHeader>
        {errorMsg && <p className="text-feedback-danger text-caption">{errorMsg}</p>}
        <DialogFooter>
          <Button type="button" variant="ghost" onClick={onClose}>Cancel</Button>
          <Button type="button" variant="destructive" onClick={submit} disabled={remove.isPending}>
            {remove.isPending ? 'Removing…' : 'Remove'}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
