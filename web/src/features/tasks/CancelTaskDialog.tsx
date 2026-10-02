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
import { useTranslation } from '@/hooks/i18n';
import { classifyTaskError, renderTaskError, type TaskErrorView } from './strings';

interface Props {
  taskId: string;
  onClose: () => void;
}

export function CancelTaskDialog({ taskId, onClose }: Props): JSX.Element {
  const { t } = useTranslation();
  const [reason, setReason] = useState('');
  const [error, setError] = useState<TaskErrorView | null>(null);
  const cancel = useCancelTask(taskId);

  const onSubmit = async () => {
    setError(null);
    try {
      await cancel.mutateAsync({ rationale: reason });
      onClose();
    } catch (e: unknown) {
      setError(classifyTaskError(e, 'tasks.dialog.cancel.failed'));
    }
  };

  return (
    <Dialog open onOpenChange={(o) => !o && onClose()}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>{t('tasks.dialog.cancel.title')}</DialogTitle>
        </DialogHeader>
        <p className="text-fg-muted text-sm">
          {t('tasks.dialog.cancel.body')}
        </p>
        <Textarea
          value={reason}
          onChange={(e) => setReason(e.target.value)}
          rows={4}
          placeholder={t('tasks.dialog.cancel.placeholder')}
        />
        {error !== null && <p className="text-danger text-sm">{renderTaskError(error, t)}</p>}
        <DialogFooter>
          <Button variant="ghost" onClick={onClose}>{t('tasks.dialog.cancel.back')}</Button>
          <Button
            variant="destructive"
            disabled={cancel.isPending}
            onClick={onSubmit}
          >
            {cancel.isPending ? t('tasks.dialog.cancel.pending') : t('tasks.dialog.cancel.confirm')}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
