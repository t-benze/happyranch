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
import { useAgentsList } from '@/hooks/agents';
import { useStartTalk } from '@/hooks/talks';
import { describeTalksError } from './strings';

interface Props {
  open: boolean;
  onClose: () => void;
  /** Called with the new (or recovered, on 409) talk id. */
  onStarted: (talkId: string) => void;
}

export function StartTalkDialog({ open, onClose, onStarted }: Props): JSX.Element {
  const start = useStartTalk();
  const agentsQuery = useAgentsList();
  const [agentName, setAgentName] = useState('');
  const [errorMsg, setErrorMsg] = useState<string | null>(null);
  const [priorOpen, setPriorOpen] = useState<string | null>(null);
  const idBase = useId();
  const agentId = `${idBase}-agent`;

  useEffect(() => {
    if (!open) {
      setAgentName('');
      setErrorMsg(null);
      setPriorOpen(null);
    }
  }, [open]);

  const submit = async () => {
    setErrorMsg(null);
    setPriorOpen(null);
    if (!agentName.trim()) {
      setErrorMsg('Agent is required.');
      return;
    }
    try {
      const resp = await start.mutateAsync({ agent_name: agentName.trim() });
      onStarted(resp.talk_id);
      onClose();
    } catch (err) {
      if (err instanceof ApiError) {
        const detail = err.detail as
          | { code?: string; prior_open_talk_id?: string }
          | undefined;
        if (detail?.code === 'talk_already_open' && detail.prior_open_talk_id) {
          setPriorOpen(detail.prior_open_talk_id);
        }
        setErrorMsg(describeTalksError(err.code, `HTTP ${err.status}`));
      } else {
        setErrorMsg(String(err));
      }
    }
  };

  return (
    <Dialog open={open} onOpenChange={(o) => { if (!o) onClose(); }}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Start talk</DialogTitle>
          <DialogDescription className="sr-only">
            Open a 1:1 founder↔agent talk.
          </DialogDescription>
        </DialogHeader>
        <FormField label="Agent" htmlFor={agentId}>
          <input
            id={agentId}
            list={`${agentId}-list`}
            value={agentName}
            onChange={(e) => setAgentName(e.target.value)}
            className="input"
            autoFocus
            placeholder="e.g. engineering_head"
          />
          <datalist id={`${agentId}-list`}>
            {(agentsQuery.data?.agents ?? []).map((a) => (
              <option key={a.name} value={a.name} />
            ))}
          </datalist>
        </FormField>
        {errorMsg && <p className="text-danger text-sm">{errorMsg}</p>}
        {priorOpen && (
          <Button
            variant="ghost"
            size="sm"
            onClick={() => { onStarted(priorOpen); onClose(); }}
          >
            Open existing talk {priorOpen}
          </Button>
        )}
        <DialogFooter>
          <Button variant="ghost" onClick={onClose}>Cancel</Button>
          <Button onClick={submit} disabled={!agentName.trim() || start.isPending}>
            {start.isPending ? 'Starting…' : 'Start talk'}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
