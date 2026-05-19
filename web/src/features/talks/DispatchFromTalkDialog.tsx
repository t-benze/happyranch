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
import { Textarea } from '@/design-system/primitives/Textarea';
import { FormField } from '@/design-system/patterns/FormField';
import { ApiError } from '@/lib/api';
import { useDispatchFromTalk } from '@/hooks/talks';
import { describeTalksError } from './strings';

interface Props {
  talkId: string;
  open: boolean;
  onClose: () => void;
  onDispatched?: (taskId: string) => void;
}

export function DispatchFromTalkDialog({
  talkId,
  open,
  onClose,
  onDispatched,
}: Props): JSX.Element {
  const dispatch = useDispatchFromTalk(talkId);
  const idBase = useId();
  const [brief, setBrief] = useState('');
  const [target, setTarget] = useState('');
  const [team, setTeam] = useState('');
  const [errorMsg, setErrorMsg] = useState<string | null>(null);

  useEffect(() => {
    if (!open) {
      setBrief('');
      setTarget('');
      setTeam('');
      setErrorMsg(null);
    }
  }, [open]);

  const submit = async () => {
    setErrorMsg(null);
    if (!brief.trim()) {
      setErrorMsg('Brief is required.');
      return;
    }
    try {
      const resp = await dispatch.mutateAsync({
        brief: brief.trim(),
        ...(target.trim() ? { target_agent: target.trim() } : {}),
        ...(team.trim() ? { team: team.trim() } : {}),
      });
      onDispatched?.(resp.task_id);
      onClose();
    } catch (err) {
      setErrorMsg(
        err instanceof ApiError
          ? describeTalksError(err.code, `HTTP ${err.status}`)
          : String(err),
      );
    }
  };

  return (
    <Dialog open={open} onOpenChange={(o) => { if (!o) onClose(); }}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Dispatch task from {talkId}</DialogTitle>
          <DialogDescription className="sr-only">
            Spawn a new task carried by the dispatching agent's team.
          </DialogDescription>
        </DialogHeader>
        <div className="flex flex-col gap-3">
          <FormField label="Brief" htmlFor={`${idBase}-brief`}>
            <Textarea
              id={`${idBase}-brief`}
              value={brief}
              onChange={(e) => setBrief(e.target.value)}
              rows={6}
              autoFocus
            />
          </FormField>
          <FormField label="Target agent (optional)" htmlFor={`${idBase}-target`}>
            <input
              id={`${idBase}-target`}
              value={target}
              onChange={(e) => setTarget(e.target.value)}
              className="input"
            />
          </FormField>
          <FormField label="Team (optional)" htmlFor={`${idBase}-team`}>
            <input
              id={`${idBase}-team`}
              value={team}
              onChange={(e) => setTeam(e.target.value)}
              className="input"
            />
          </FormField>
        </div>
        {errorMsg && <p className="text-danger text-sm">{errorMsg}</p>}
        <DialogFooter>
          <Button variant="ghost" onClick={onClose}>Cancel</Button>
          <Button onClick={submit} disabled={!brief.trim() || dispatch.isPending}>
            {dispatch.isPending ? 'Dispatching…' : 'Confirm dispatch'}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
