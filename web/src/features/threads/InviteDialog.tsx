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
import { useInviteAgent } from '@/hooks/threads';
import { RecipientsInput } from './RecipientsInput';
import { describeError } from './strings';
import type { AgentSummary } from '@/lib/api/types';

interface Props {
  threadId: string;
  open: boolean;
  onClose: () => void;
  agents?: AgentSummary[];
}

export function InviteDialog({ threadId, open, onClose, agents = [] }: Props): JSX.Element {
  const invite = useInviteAgent(threadId);
  const [recipientsRaw, setRecipientsRaw] = useState('');
  const [errorMsg, setErrorMsg] = useState<string | null>(null);
  const nameId = useId();

  useEffect(() => {
    if (!open) return;
    setRecipientsRaw('');
    setErrorMsg(null);
  }, [open]);

  const submit = async (e?: React.FormEvent) => {
    e?.preventDefault();
    setErrorMsg(null);
    // Take the first non-empty token as the single agent name to invite.
    const firstName = recipientsRaw
      .split(',')
      .map((s) => s.trim())
      .find(Boolean);
    if (!firstName) {
      setErrorMsg('Agent name is required.');
      return;
    }
    try {
      await invite.mutateAsync({ agent_name: firstName });
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
          <DialogTitle>Invite participant</DialogTitle>
          <DialogDescription className="sr-only">
            Invite an additional agent or founder to this thread.
          </DialogDescription>
        </DialogHeader>
        <form onSubmit={submit} className="flex flex-col gap-3">
          <FormField label="Agent name" htmlFor={nameId} error={errorMsg ?? undefined}>
            <RecipientsInput
              id={nameId}
              value={recipientsRaw}
              onChange={setRecipientsRaw}
              agents={agents}
              placeholder="agent_a, agent_b"
            />
          </FormField>
          <DialogFooter>
            <Button type="button" variant="ghost" onClick={onClose}>Cancel</Button>
            <Button type="submit" disabled={invite.isPending}>
              {invite.isPending ? 'Inviting…' : 'Invite'}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}
