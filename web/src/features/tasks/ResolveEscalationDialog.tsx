import { useState } from 'react';
import {
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/design-system/primitives/Dialog';
import { Button } from '@/design-system/primitives/Button';
import { Textarea } from '@/design-system/primitives/Textarea';
import { useResolveEscalation } from '@/hooks/tasks';
import { TASKS_ERROR_STRINGS } from './strings';

interface Props {
  taskId: string;
  onClose: () => void;
}

export function ResolveEscalationDialog({ taskId, onClose }: Props): JSX.Element {
  const [decision, setDecision] = useState<'approve' | 'reject'>('approve');
  const [rationale, setRationale] = useState('');
  const [error, setError] = useState<string | null>(null);
  const resolve = useResolveEscalation(taskId);

  const onSubmit = async () => {
    setError(null);
    if (!rationale.trim()) {
      setError('Rationale is required.');
      return;
    }
    try {
      await resolve.mutateAsync({ decision, rationale });
      onClose();
    } catch (e: unknown) {
      const code = (e as { code?: string }).code;
      setError(code ? (TASKS_ERROR_STRINGS[code] ?? code) : 'Resolve failed.');
    }
  };

  return (
    <Dialog open onOpenChange={(o) => !o && onClose()}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Resolve escalation</DialogTitle>
        </DialogHeader>
        <fieldset className="flex gap-4">
          <label className="text-fg flex items-center gap-2">
            <input
              type="radio"
              name="decision"
              value="approve"
              checked={decision === 'approve'}
              onChange={() => setDecision('approve')}
            />
            Approve
          </label>
          <label className="text-fg flex items-center gap-2">
            <input
              type="radio"
              name="decision"
              value="reject"
              checked={decision === 'reject'}
              onChange={() => setDecision('reject')}
            />
            Reject
          </label>
        </fieldset>
        <Textarea
          value={rationale}
          onChange={(e) => setRationale(e.target.value)}
          rows={4}
          placeholder="Rationale (required)"
        />
        {error && <p className="text-danger text-sm">{error}</p>}
        <DialogFooter>
          <Button variant="ghost" onClick={onClose}>Cancel</Button>
          <Button disabled={resolve.isPending} onClick={onSubmit}>
            {resolve.isPending ? 'Resolving…' : 'Resolve'}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
