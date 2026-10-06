import { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import {
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/design-system/primitives/Dialog';
import { Button } from '@/design-system/primitives/Button';
import { Textarea } from '@/design-system/primitives/Textarea';
import { Input } from '@/design-system/primitives/Input';
import { useRevisitTask, useTasksRoutes } from '@/hooks/tasks';
import { useTranslation } from '@/hooks/i18n';
import { classifyTaskError, renderTaskError, type TaskErrorView } from './strings';

interface Props {
  taskId: string;
  onClose: () => void;
}

export function RevisitTaskDialog({ taskId, onClose }: Props): JSX.Element {
  const { t } = useTranslation();
  const [note, setNote] = useState('');
  const [sessionTimeout, setSessionTimeout] = useState('');
  const [error, setError] = useState<TaskErrorView | null>(null);
  const revisit = useRevisitTask(taskId);
  const navigate = useNavigate();
  const routes = useTasksRoutes();

  const onSubmit = async () => {
    setError(null);
    const sst = sessionTimeout.trim();
    if (sst && !/^\d+$/.test(sst)) {
      setError({ kind: 'message', key: 'tasks.dialog.revisit.invalidTimeout' });
      return;
    }
    try {
      const out = await revisit.mutateAsync({
        founder_note: note || undefined,
        session_timeout_seconds: sst ? Number(sst) : undefined,
      });
      if (out.task_id) navigate(routes.detail(out.task_id));
      else onClose();
    } catch (e: unknown) {
      setError(classifyTaskError(e, 'tasks.dialog.revisit.failed'));
    }
  };

  return (
    <Dialog open onOpenChange={(o) => !o && onClose()}>
      <DialogContent closeLabel={t('common.close')}>
        <DialogHeader>
          <DialogTitle>{t('tasks.dialog.revisit.title')}</DialogTitle>
        </DialogHeader>
        <p className="text-fg-muted text-sm">
          {t('tasks.dialog.revisit.body')}
        </p>
        <Textarea
          value={note}
          onChange={(e) => setNote(e.target.value)}
          rows={3}
          placeholder={t('tasks.dialog.revisit.notePlaceholder')}
        />
        <Input
          value={sessionTimeout}
          onChange={(e) => setSessionTimeout(e.target.value)}
          placeholder={t('tasks.dialog.revisit.timeoutPlaceholder')}
        />
        {error !== null && <p className="text-danger text-sm">{renderTaskError(error, t)}</p>}
        <DialogFooter>
          <Button variant="ghost" onClick={onClose}>{t('common.cancel')}</Button>
          <Button disabled={revisit.isPending} onClick={onSubmit}>
            {revisit.isPending ? t('tasks.dialog.revisit.pending') : t('tasks.dialog.revisit.confirm')}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
