import { useEffect, useState } from 'react';
import { Modal } from '@/components/Modal';
import { Button } from '@/components/Button';
import { ApiError } from '@/lib/api';
import { useOrgSlug } from '@/lib/orgSlug';
import { useArchiveThread } from './hooks';
import { describeError } from './strings';

interface Props {
  threadId: string;
  open: boolean;
  onClose: () => void;
}

export function ArchiveDialog({ threadId, open, onClose }: Props): JSX.Element {
  const archive = useArchiveThread(useOrgSlug(), threadId);
  const [summary, setSummary] = useState('');
  const [requestCloseOuts, setRequestCloseOuts] = useState(true);
  const [errorMsg, setErrorMsg] = useState<string | null>(null);

  useEffect(() => {
    if (!open) return;
    setSummary('');
    setRequestCloseOuts(true);
    setErrorMsg(null);
  }, [open]);

  const submit = async () => {
    setErrorMsg(null);
    if (!summary.trim()) {
      setErrorMsg('Summary is required.');
      return;
    }
    try {
      await archive.mutateAsync({
        summary: summary.trim(),
        request_close_outs: requestCloseOuts,
      });
      onClose();
    } catch (err) {
      setErrorMsg(
        err instanceof ApiError ? describeError(err.code, `HTTP ${err.status}`) : String(err),
      );
    }
  };

  return (
    <Modal title="Archive thread" open={open} onClose={onClose}>
      <div className="flex flex-col gap-3">
        <label className="flex flex-col gap-1 text-xs">
          <span className="text-fg-muted">Founder summary (will be saved to transcript)</span>
          <textarea
            value={summary}
            onChange={(e) => setSummary(e.target.value)}
            rows={5}
            autoFocus
            className="input resize-y"
          />
        </label>
        <label className="flex items-center gap-2 text-xs text-fg">
          <input
            type="checkbox"
            checked={requestCloseOuts}
            onChange={(e) => setRequestCloseOuts(e.target.checked)}
          />
          Request close-outs from each participant
        </label>
        {errorMsg && <p className="text-xs text-tier-red">{errorMsg}</p>}
        <div className="flex justify-end gap-2">
          <Button variant="ghost" onClick={onClose}>Cancel</Button>
          <Button onClick={submit} disabled={archive.isPending}>
            {archive.isPending ? 'Archiving…' : 'Archive'}
          </Button>
        </div>
      </div>
    </Modal>
  );
}
