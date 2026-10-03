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
import { useTranslation } from '@/hooks/i18n';
import { classifyTaskError, renderTaskError, type TaskErrorView } from './strings';

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
  const { t } = useTranslation();
  const [rationale, setRationale] = useState('');
  const [error, setError] = useState<TaskErrorView | null>(null);
  const resolve = useResolveEscalation(taskId);
  const isContinue = intent === 'continue';

  const onSubmit = async () => {
    setError(null);
    try {
      const body: { decision: 'supersede' | 'continue'; rationale: string; brief?: string } = {
        decision: intent as 'supersede' | 'continue',
        rationale,
      };
      // For supersede, the rationale textarea content is the brief.
      if (!isContinue) {
        body.brief = rationale;
      }
      await resolve.mutateAsync(body);
      onClose();
    } catch (e: unknown) {
      setError(classifyTaskError(e, 'tasks.dialog.resolve.failed'));
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
            {isContinue ? t('tasks.dialog.resolve.titleContinue') : t('tasks.dialog.resolve.titleSupersede')}
          </DialogTitle>
        </DialogHeader>
        <Textarea
          value={rationale}
          onChange={(e) => setRationale(e.target.value)}
          rows={4}
          placeholder={
            isContinue
              ? t('tasks.dialog.resolve.placeholderContinue')
              : t('tasks.dialog.resolve.placeholderSupersede')
          }
        />
        {error !== null && <p className="text-danger text-sm">{renderTaskError(error, t)}</p>}
        <DialogFooter>
          <Button variant="ghost" onClick={onClose}>
            {t('common.close')}
          </Button>
          <Button disabled={submitDisabled} onClick={onSubmit}>
            {isContinue
              ? resolve.isPending
                ? t('tasks.dialog.resolve.continuing')
                : t('tasks.dialog.resolve.confirmContinue')
              : resolve.isPending
                ? t('tasks.dialog.resolve.superseding')
                : t('tasks.dialog.resolve.confirmSupersede')}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
