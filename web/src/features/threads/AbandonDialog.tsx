import { useEffect, useState } from 'react';
import { Modal } from '@/components/Modal';
import { Button } from '@/components/Button';
import { ApiError } from '@/lib/api';
import { useOrgSlug } from '@/lib/orgSlug';
import { useAbandonThread } from './hooks';
import { describeError } from './strings';

interface Props {
  threadId: string;
  open: boolean;
  onClose: () => void;
}

export function AbandonDialog({ threadId, open, onClose }: Props): JSX.Element {
  const abandon = useAbandonThread(useOrgSlug(), threadId);
  const [reason, setReason] = useState('');
  const [errorMsg, setErrorMsg] = useState<string | null>(null);

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
    <Modal title="Abandon thread" open={open} onClose={onClose}>
      <div className="flex flex-col gap-3">
        <p className="text-xs text-fg-muted">
          Abandons the thread without close-outs. Use this when the thread is no longer useful.
        </p>
        <label className="flex flex-col gap-1 text-xs">
          <span className="text-fg-muted">Reason</span>
          <input
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
        </label>
        {errorMsg && <p className="text-xs text-tier-red">{errorMsg}</p>}
        <div className="flex justify-end gap-2">
          <Button variant="ghost" onClick={onClose}>Cancel</Button>
          <Button variant="danger" onClick={submit} disabled={abandon.isPending}>
            {abandon.isPending ? 'Abandoning…' : 'Abandon'}
          </Button>
        </div>
      </div>
    </Modal>
  );
}
