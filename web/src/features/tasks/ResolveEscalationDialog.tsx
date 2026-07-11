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
   * Which escalation-resolution decision this dialog drives.
   * 'continue' resumes the task → pending and REQUIRES a rationale.
   * 'supersede' mints a successor task with a brief and closes the
   * predecessor as superseded (THR-080). Cancel is NOT part of the
   * resolution vocabulary — use the generic /cancel route for that.
   * Defaults to 'continue'.
   */
  intent?: 'supersede' | 'continue';
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
      const body: { decision: string; rationale: string; brief?: string } = {
        decision: intent,
        rationale,
      };
      // For supersede, the rationale textarea content is the brief.
      if (!isContinue) {
        body.brief = rationale;
      }
      await resolve.mutateAsync(body);
      onClose();
    } catch (e: unknown) {
      const code = (e as { code?: string }).code;
      setError(code ? (TASKS_ERROR_STRINGS[code] ?? code) : 'Resolve failed.');
    }
  };

  const textEmpty = rationale.trim() === '';
  // Both continue and supersede require the textarea content.
  const submitDisabled = textEmpty || resolve.isPending;

  return (
    <Dialog open onOpenChange={(o) => !o && onClose()}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>
            {isContinue ? 'Continue task' : 'Supersede task'}
          </DialogTitle>
        </DialogHeader>
        <Textarea
          value={rationale}
          onChange={(e) => setRationale(e.target.value)}
          rows={4}
          placeholder={
            isContinue ? 'Rationale (required)' : 'Successor task brief (required)'
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
                ? 'Superseding…'
                : 'Supersede task'}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
