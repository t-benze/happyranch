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
import { useInviteAgent } from './hooks';
import { describeError } from './strings';

interface Props {
  threadId: string;
  open: boolean;
  onClose: () => void;
}

export function InviteDialog({ threadId, open, onClose }: Props): JSX.Element {
  const invite = useInviteAgent(useOrgSlug(), threadId);
  const [name, setName] = useState('');
  const [errorMsg, setErrorMsg] = useState<string | null>(null);
  const nameId = useId();

  useEffect(() => {
    if (!open) return;
    setName('');
    setErrorMsg(null);
  }, [open]);

  const submit = async () => {
    setErrorMsg(null);
    if (!name.trim()) {
      setErrorMsg('Agent name is required.');
      return;
    }
    try {
      await invite.mutateAsync({ agent: name.trim() });
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
        <div className="flex flex-col gap-3">
          <FormField label="Agent name" htmlFor={nameId} error={errorMsg ?? undefined}>
            <input
              id={nameId}
              type="text"
              value={name}
              onChange={(e) => setName(e.target.value)}
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
          <Button onClick={submit} disabled={invite.isPending}>
            {invite.isPending ? 'Inviting…' : 'Invite'}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
