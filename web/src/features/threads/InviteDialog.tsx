import { useEffect, useState } from 'react';
import { Modal } from '@/components/Modal';
import { Button } from '@/components/Button';
import { ApiError } from '@/lib/api';
import { useInviteAgent } from './hooks';
import { describeError } from './strings';

interface Props {
  threadId: string;
  open: boolean;
  onClose: () => void;
}

export function InviteDialog({ threadId, open, onClose }: Props): JSX.Element {
  const invite = useInviteAgent(useSlugFromHook(), threadId);
  const [name, setName] = useState('');
  const [errorMsg, setErrorMsg] = useState<string | null>(null);

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
    <Modal title="Invite participant" open={open} onClose={onClose}>
      <div className="flex flex-col gap-3">
        <label className="flex flex-col gap-1 text-xs">
          <span className="text-fg-muted">Agent name</span>
          <input
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
        </label>
        {errorMsg && <p className="text-xs text-tier-red">{errorMsg}</p>}
        <div className="flex justify-end gap-2">
          <Button variant="ghost" onClick={onClose}>Cancel</Button>
          <Button onClick={submit} disabled={invite.isPending}>
            {invite.isPending ? 'Inviting…' : 'Invite'}
          </Button>
        </div>
      </div>
    </Modal>
  );
}

import { useOrgSlug } from '@/lib/orgSlug';
function useSlugFromHook() {
  return useOrgSlug();
}
