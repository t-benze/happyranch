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
import { TASKS_ERROR_STRINGS } from './strings';

interface Props {
  taskId: string;
  onClose: () => void;
}

export function RevisitTaskDialog({ taskId, onClose }: Props): JSX.Element {
  const [note, setNote] = useState('');
  const [sessionTimeout, setSessionTimeout] = useState('');
  const [error, setError] = useState<string | null>(null);
  const revisit = useRevisitTask(taskId);
  const navigate = useNavigate();
  const routes = useTasksRoutes();

  const onSubmit = async () => {
    setError(null);
    const sst = sessionTimeout.trim();
    if (sst && !/^\d+$/.test(sst)) {
      setError('Session timeout must be a positive integer.');
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
      const code = (e as { code?: string }).code;
      setError(code ? (TASKS_ERROR_STRINGS[code] ?? code) : 'Revisit failed.');
    }
  };

  return (
    <Dialog open onOpenChange={(o) => !o && onClose()}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Revisit task</DialogTitle>
        </DialogHeader>
        <p className="text-fg-muted text-sm">
          Spawns a new root task inheriting the brief + team. The original task stays frozen.
        </p>
        <Textarea
          value={note}
          onChange={(e) => setNote(e.target.value)}
          rows={3}
          placeholder="Note for the new root (optional)"
        />
        <Input
          value={sessionTimeout}
          onChange={(e) => setSessionTimeout(e.target.value)}
          placeholder="Session timeout (seconds, optional)"
        />
        {error && <p className="text-danger text-sm">{error}</p>}
        <DialogFooter>
          <Button variant="ghost" onClick={onClose}>Cancel</Button>
          <Button disabled={revisit.isPending} onClick={onSubmit}>
            {revisit.isPending ? 'Revisiting…' : 'Revisit'}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
