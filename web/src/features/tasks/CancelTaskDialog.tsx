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
import { useCancelTask } from '@/hooks/tasks';
import { TASKS_ERROR_STRINGS } from './strings';

interface Props {
  taskId: string;
  onClose: () => void;
}

export function CancelTaskDialog({ taskId, onClose }: Props): JSX.Element {
  const [reason, setReason] = useState('');
  const [error, setError] = useState<string | null>(null);
  const cancel = useCancelTask(taskId);

  const onSubmit = async () => {
    setError(null);
    try {
      await cancel.mutateAsync({ reason });
      onClose();
    } catch (e: unknown) {
      const code = (e as { code?: string }).code;
      setError(code ? (TASKS_ERROR_STRINGS[code] ?? code) : 'Cancel failed.');
    }
  };

  return (
    <Dialog open onOpenChange={(o) => !o && onClose()}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Cancel task</DialogTitle>
        </DialogHeader>
        <p className="text-fg-muted text-sm">
          Reason (required). The agent's current session will be terminated.
        </p>
        <Textarea
          value={reason}
          onChange={(e) => setReason(e.target.value)}
          rows={4}
          placeholder="Reason for cancellation"
        />
        {error && <p className="text-danger text-sm">{error}</p>}
        <DialogFooter>
          <Button variant="ghost" onClick={onClose}>Back</Button>
          <Button
            variant="destructive"
            disabled={!reason.trim() || cancel.isPending}
            onClick={onSubmit}
          >
            {cancel.isPending ? 'Cancelling…' : 'Cancel task'}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
