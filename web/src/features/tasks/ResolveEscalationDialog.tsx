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
  /**
   * Which escalation-resolution decision this dialog drives. BOTH decisions
   * POST to /tasks/{id}/resolve-escalation via useResolveEscalation (THR-075),
   * NOT the generic /cancel route: 'continue' resumes the task → pending and
   * REQUIRES a rationale; 'cancel' terminates the escalated task → cancelled
   * with an OPTIONAL rationale (consumes the Feishu escalation notification and
   * writes the escalation-resolved audit row). Defaults to 'continue'.
   */
  intent?: 'continue' | 'cancel';
}

export function ResolveEscalationDialog({
  taskId,
  onClose,
  intent = 'continue',
}: Props): JSX.Element {
  const [rationale, setRationale] = useState('');
  const [error, setError] = useState<string | null>(null);
  const resolve = useResolveEscalation(taskId);
  const isContinue = intent === 'continue';

  const onSubmit = async () => {
    setError(null);
    try {
      await resolve.mutateAsync({ decision: intent, rationale });
      onClose();
    } catch (e: unknown) {
      const code = (e as { code?: string }).code;
      setError(code ? (TASKS_ERROR_STRINGS[code] ?? code) : 'Resolve failed.');
    }
  };

  const rationaleEmpty = rationale.trim() === '';
  // Continue requires a rationale; cancel does not.
  const submitDisabled = (isContinue && rationaleEmpty) || resolve.isPending;

  return (
    <Dialog open onOpenChange={(o) => !o && onClose()}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>
            {isContinue ? 'Continue task' : 'Cancel escalated task'}
          </DialogTitle>
        </DialogHeader>
        <Textarea
          value={rationale}
          onChange={(e) => setRationale(e.target.value)}
          rows={4}
          placeholder={
            isContinue ? 'Rationale (required)' : 'Rationale (optional)'
          }
        />
        {error && <p className="text-danger text-sm">{error}</p>}
        <DialogFooter>
          <Button variant="ghost" onClick={onClose}>
            Close
          </Button>
          <Button disabled={submitDisabled} onClick={onSubmit}>
            {isContinue
              ? resolve.isPending
                ? 'Continuing…'
                : 'Continue task'
              : resolve.isPending
                ? 'Cancelling…'
                : 'Cancel task'}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
