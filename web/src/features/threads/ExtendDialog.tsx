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
import { useOrgSlug } from '@/lib/orgSlug';
import { useExtendCap } from './hooks';
import { describeError } from './strings';

interface Props {
  threadId: string;
  currentCap: number;
  open: boolean;
  onClose: () => void;
}

export function ExtendDialog({
  threadId,
  currentCap,
  open,
  onClose,
}: Props): JSX.Element {
  const extend = useExtendCap(useOrgSlug(), threadId);
  const [cap, setCap] = useState(currentCap + 100);
  const [errorMsg, setErrorMsg] = useState<string | null>(null);

  useEffect(() => {
    if (!open) return;
    setCap(currentCap + 100);
    setErrorMsg(null);
  }, [open, currentCap]);

  const submit = async () => {
    setErrorMsg(null);
    if (cap <= currentCap) {
      setErrorMsg(`Must be greater than current cap (${currentCap}).`);
      return;
    }
    try {
      await extend.mutateAsync({ new_cap: cap });
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
          <DialogTitle>Extend turn cap</DialogTitle>
          <DialogDescription className="sr-only">
            Raise the maximum number of turns allowed on this thread.
          </DialogDescription>
        </DialogHeader>
        <div className="flex flex-col gap-3">
          <p className="text-xs text-fg-muted">
            Current cap: <strong className="text-fg">{currentCap}</strong>
          </p>
          <label className="flex flex-col gap-1 text-xs">
            <span className="text-fg-muted">New cap</span>
            <input
              type="number"
              value={cap}
              min={currentCap + 1}
              onChange={(e) => setCap(parseInt(e.target.value, 10) || 0)}
              autoFocus
              className="input"
            />
          </label>
          {errorMsg && <p className="text-xs text-tier-red">{errorMsg}</p>}
        </div>
        <DialogFooter>
          <Button variant="ghost" onClick={onClose}>Cancel</Button>
          <Button onClick={submit} disabled={extend.isPending}>
            {extend.isPending ? 'Saving…' : 'Save'}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
