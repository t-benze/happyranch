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
    // Parse comma-separated tokens, trim, discard empties, deduplicate
    // preserving selection order — RecipientsInput builds comma-separated
    // selected names, and the backend POST /threads/{id}/invite accepts
    // one { agent_name } request per participant. Submit every selected
    // name through that unchanged single-agent API sequentially.
    const names = recipientsRaw
      .split(',')
      .map((s) => s.trim())
      .filter(Boolean);
    if (names.length === 0) {
      setErrorMsg('Agent name is required.');
      return;
    }
    const uniqueNames = [...new Set(names)];
    try {
      // Sequential awaits — deterministic order, matches the non-batch
      // server contract one-agent-per-request.
      for (const name of uniqueNames) {
        await invite.mutateAsync({ agent_name: name });
      }
      onClose();
    } catch (err) {
      // Honest partial failure: any succeeding invite mutated before the
      // failure landed — the dialog stays open so the user can retry or
      // close. useInviteAgent onSuccess invalidates ['thread', slug, threadId]
      // on every individual success, so successful invites are reflected.
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
