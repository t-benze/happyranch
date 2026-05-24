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
import { Input } from '@/design-system/primitives/Input';
import { ApiError } from '@/lib/api';
import { useRunScript } from '@/hooks/scripts';
import type { ScriptRequest } from '@/lib/api/types';

interface Props {
  sr: ScriptRequest;
  open: boolean;
  onClose: () => void;
  onSuccess?: () => void;
}

export function RunScriptDialog({ sr, open, onClose, onSuccess }: Props): JSX.Element {
  const run = useRunScript();
  const [cwdOverride, setCwdOverride] = useState('');
  const [timeoutSeconds, setTimeoutSeconds] = useState(sr.timeout_seconds);
  const [errorMsg, setErrorMsg] = useState<string | null>(null);
  const cwdId = useId();
  const timeoutId = useId();

  useEffect(() => {
    if (!open) return;
    setCwdOverride('');
    setTimeoutSeconds(sr.timeout_seconds);
    setErrorMsg(null);
  }, [open, sr.timeout_seconds]);

  const submit = async () => {
    setErrorMsg(null);
    const body: { cwd_override?: string; timeout_seconds?: number } = {};
    if (cwdOverride.trim()) body.cwd_override = cwdOverride.trim();
    if (timeoutSeconds !== sr.timeout_seconds) body.timeout_seconds = timeoutSeconds;
    try {
      await run.mutateAsync({ srId: sr.id, body });
      onSuccess?.();
      onClose();
    } catch (err) {
      setErrorMsg(
        err instanceof ApiError
          ? `Error ${err.status}: ${err.message}`
          : String(err),
      );
    }
  };

  return (
    <Dialog open={open} onOpenChange={(o) => { if (!o) onClose(); }}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Run {sr.id}</DialogTitle>
          <DialogDescription className="sr-only">
            Approve and run this script request. The script will execute immediately.
          </DialogDescription>
        </DialogHeader>

        {/* Script preview */}
        <div className="space-y-3">
          <div>
            <p className="text-fg-muted mb-1 text-xs font-medium uppercase tracking-wider">
              Script
              <span className="ml-1 normal-case">
                ({sr.interpreter}{sr.cwd_hint ? ` · cwd hint: ${sr.cwd_hint}` : ''})
              </span>
            </p>
            <pre className="bg-surface-canvas text-fg max-h-40 overflow-y-auto rounded p-3 text-xs whitespace-pre">
              {sr.script_text}
            </pre>
          </div>

          <FormField label="Working directory override" htmlFor={cwdId}>
            <Input
              id={cwdId}
              type="text"
              placeholder={sr.cwd_hint ?? 'default (agent workspace)'}
              value={cwdOverride}
              onChange={(e) => setCwdOverride(e.target.value)}
            />
          </FormField>

          <FormField label="Timeout (seconds)" htmlFor={timeoutId}>
            <Input
              id={timeoutId}
              type="number"
              min={1}
              value={timeoutSeconds}
              onChange={(e) => setTimeoutSeconds(Number(e.target.value))}
            />
          </FormField>

          {errorMsg && (
            <p className="text-fg-danger text-sm">{errorMsg}</p>
          )}
        </div>

        <DialogFooter>
          <Button variant="ghost" onClick={onClose}>
            Cancel
          </Button>
          <Button
            variant="default"
            onClick={submit}
            disabled={run.isPending || timeoutSeconds < 1}
          >
            {run.isPending ? 'Running…' : 'Run'}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
