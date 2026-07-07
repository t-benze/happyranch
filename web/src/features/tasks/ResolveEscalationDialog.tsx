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
  const [rationale, setRationale] = useState('');
  const [error, setError] = useState<string | null>(null);
  const resolveContinue = useResolveEscalation(taskId);

  const onContinue = async () => {
    setError(null);
    try {
      await resolveContinue.mutateAsync({ decision: 'continue', rationale });
      onClose();
    } catch (e: unknown) {
      const code = (e as { code?: string }).code;
      setError(code ? (TASKS_ERROR_STRINGS[code] ?? code) : 'Resolve failed.');
    }
  };

  const onCancel = async () => {
    setError(null);
    try {
      await resolveContinue.mutateAsync({ decision: 'cancel', rationale });
      onClose();
    } catch (e: unknown) {
      const code = (e as { code?: string }).code;
      setError(code ? (TASKS_ERROR_STRINGS[code] ?? code) : 'Resolve failed.');
    }
  };

  const rationaleEmpty = rationale.trim() === '';

  return (
    <Dialog open onOpenChange={(o) => !o && onClose()}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Resolve escalation</DialogTitle>
        </DialogHeader>
        <Textarea
          value={rationale}
          onChange={(e) => setRationale(e.target.value)}
          rows={4}
          placeholder="Rationale (required for continue)"
        />
        {error && <p className="text-danger text-sm">{error}</p>}
        <DialogFooter>
          <Button variant="ghost" onClick={onCancel}>Cancel</Button>
          <Button
            disabled={rationaleEmpty || resolveContinue.isPending}
            onClick={onContinue}
          >
            {resolveContinue.isPending ? 'Continuing…' : 'Continue'}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
