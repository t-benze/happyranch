import { useEffect, useState } from 'react';
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
import { useRevisitTask, useTask, useTasksRoutes } from '@/hooks/tasks';
import { useTaskSelectionOptions } from '@/hooks/task-selection';
import type { InvalidTaskSelectionDetail, TaskRecord } from '@/lib/api/types';
import { useTranslation } from '@/hooks/i18n';
import { classifyTaskError, renderTaskError, type TaskErrorView } from './strings';
import { selectionBody, selectionRefusal, type CarriedSelection, type SelectionDraft } from './taskSelection';
import { TaskSelectionFields, TaskSelectionRefusal } from './TaskSelectionFields';

interface Props {
  taskId: string;
  onClose: () => void;
}

/** Same bound as the daemon's `walk_ancestors(max_hops=20)`. */
const MAX_ANCESTOR_HOPS = 20;

type PredecessorRoot =
  | { status: 'loading' }
  | { status: 'unavailable' }
  | { status: 'ready'; task: TaskRecord };

/**
 * The task POST /tasks/{id}/revisit actually restarts: the daemon walks
 * `parent_task_id` from the flagged task to its ancestor root and carries and
 * validates THAT root's override and assigned agent. This walks the same
 * edge with the existing task read, one hop per loaded task.
 */
function usePredecessorRoot(taskId: string): PredecessorRoot {
  const [walk, setWalk] = useState({ from: taskId, id: taskId, hops: 1 });
  const step = walk.from === taskId ? walk : { from: taskId, id: taskId, hops: 1 };
  const query = useTask(step.id);
  const loaded = query.data?.task_id === step.id ? query.data : undefined;
  const parentId = loaded ? loaded.parent_task_id ?? null : undefined;
  const tooDeep = !!parentId && step.hops >= MAX_ANCESTOR_HOPS;
  useEffect(() => {
    if (parentId && !tooDeep) setWalk({ from: taskId, id: parentId, hops: step.hops + 1 });
  }, [taskId, parentId, tooDeep, step.hops]);
  if (loaded && parentId === null) return { status: 'ready', task: loaded };
  if (tooDeep || (!loaded && query.isError)) return { status: 'unavailable' };
  return { status: 'loading' };
}

export function RevisitTaskDialog({ taskId, onClose }: Props): JSX.Element {
  const { t } = useTranslation();
  const [note, setNote] = useState('');
  const [sessionTimeout, setSessionTimeout] = useState('');
  const [error, setError] = useState<TaskErrorView | null>(null);
  const [refusal, setRefusal] = useState<InvalidTaskSelectionDetail | null>(null);
  const revisit = useRevisitTask(taskId);
  // The daemon restarts the predecessor ROOT, so the root's stored override is
  // the carry baseline and the root's agent decides the options. `edited` is
  // null until the founder changes a field, so the form follows that root.
  const root = usePredecessorRoot(taskId);
  const rootTask = root.status === 'ready' ? root.task : null;
  const carried: CarriedSelection | null = rootTask
    ? { model: rootTask.model_override ?? null, effort: rootTask.reasoning_effort_override ?? null }
    : null;
  const [edited, setEdited] = useState<SelectionDraft | null>(null);
  const draft: SelectionDraft = edited ?? { model: carried?.model ?? '', effort: carried?.effort ?? '' };
  const options = useTaskSelectionOptions(rootTask?.assigned_agent ?? null);
  const navigate = useNavigate();
  const routes = useTasksRoutes();

  const onSubmit = async () => {
    // Without the root's saved override the form cannot say what an omitted
    // field would carry, so nothing is sent until it is known.
    if (carried === null) return;
    setError(null);
    setRefusal(null);
    const sst = sessionTimeout.trim();
    if (sst && !/^\d+$/.test(sst)) {
      setError({ kind: 'message', key: 'tasks.dialog.revisit.invalidTimeout' });
      return;
    }
    try {
      const out = await revisit.mutateAsync({
        founder_note: note || undefined,
        session_timeout_seconds: sst ? Number(sst) : undefined,
        // Untouched fields are omitted (carry); a cleared field sends null.
        ...selectionBody(draft, carried),
      });
      if (out.new_root_task_id) navigate(routes.detail(out.new_root_task_id));
      else onClose();
    } catch (e: unknown) {
      const refused = selectionRefusal(e);
      if (refused !== null) setRefusal(refused);
      else setError(classifyTaskError(e, 'tasks.dialog.revisit.failed'));
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
        {rootTask !== null && rootTask.task_id !== taskId && (
          <p className="text-text-muted text-xs" data-revisit-root={rootTask.task_id}>
            {t('tasks.selection.rootNote', { root: rootTask.task_id })}
          </p>
        )}
        {carried !== null ? (
          <TaskSelectionFields
            idPrefix="revisit-task"
            options={options}
            draft={draft}
            onChange={setEdited}
            carried={carried}
            disabled={revisit.isPending}
          />
        ) : (
          <p className="text-text-muted text-xs" data-revisit-root-state={root.status}>
            {t(root.status === 'unavailable' ? 'tasks.selection.rootUnavailable' : 'tasks.selection.rootLoading')}
          </p>
        )}
        {refusal !== null && <TaskSelectionRefusal refusal={refusal} />}
        {error !== null && <p className="text-danger text-sm">{renderTaskError(error, t)}</p>}
        <DialogFooter>
          <Button variant="ghost" onClick={onClose}>{t('common.cancel')}</Button>
          <Button disabled={revisit.isPending || carried === null} onClick={onSubmit}>
            {revisit.isPending ? t('tasks.dialog.revisit.pending') : t('tasks.dialog.revisit.confirm')}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
