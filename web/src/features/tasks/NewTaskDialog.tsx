/**
 * New task dialog (THR-356 PR3).
 *
 * Submits POST /tasks. Model and effort are optional: leaving both unset adds
 * no selection key to the request, so the task runs exactly as an ordinary
 * submission. Options come from the chosen agent's executor; with no agent
 * chosen the daemon picks the default team's manager, whose executor the page
 * does not guess, so both fields stay disabled until an agent is chosen.
 */
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
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/design-system/primitives/Select';
import { useAgentsList } from '@/hooks/agents';
import { useSubmitTask, useTasksRoutes } from '@/hooks/tasks';
import { useTaskSelectionOptions } from '@/hooks/task-selection';
import { useTranslation } from '@/hooks/i18n';
import type { InvalidTaskSelectionDetail } from '@/lib/api/types';
import { classifyTaskError, renderTaskError, type TaskErrorView } from './strings';
import { selectionBody, selectionRefusal, type SelectionDraft } from './taskSelection';
import { TaskSelectionFields, TaskSelectionRefusal } from './TaskSelectionFields';

const DEFAULT_OWNER = '__default_owner__';

export function NewTaskDialog({ onClose }: { onClose: () => void }): JSX.Element {
  const { t } = useTranslation();
  const [brief, setBrief] = useState('');
  const [owner, setOwner] = useState('');
  const [draft, setDraft] = useState<SelectionDraft>({ model: '', effort: '' });
  const [error, setError] = useState<TaskErrorView | null>(null);
  const [refusal, setRefusal] = useState<InvalidTaskSelectionDetail | null>(null);
  const agents = useAgentsList();
  const options = useTaskSelectionOptions(owner || null);
  const submit = useSubmitTask();
  const navigate = useNavigate();
  const routes = useTasksRoutes();

  const onSubmit = async () => {
    setError(null);
    setRefusal(null);
    if (brief.trim() === '') {
      setError({ kind: 'message', key: 'tasks.dialog.new.briefRequired' });
      return;
    }
    const selection = selectionBody(draft);
    try {
      const out = await submit.mutateAsync({
        brief,
        ...(owner ? { owner } : {}),
        ...(typeof selection.model === 'string' ? { model: selection.model } : {}),
        ...(typeof selection.reasoning_effort === 'string' ? { reasoning_effort: selection.reasoning_effort } : {}),
      });
      if (out.task_id) navigate(routes.detail(out.task_id));
      else onClose();
    } catch (e: unknown) {
      const refused = selectionRefusal(e);
      if (refused !== null) setRefusal(refused);
      else setError(classifyTaskError(e, 'tasks.dialog.new.failed'));
    }
  };

  return (
    <Dialog open onOpenChange={(o) => !o && onClose()}>
      <DialogContent closeLabel={t('common.close')}>
        <DialogHeader>
          <DialogTitle>{t('tasks.dialog.new.title')}</DialogTitle>
        </DialogHeader>
        <div className="min-w-0">
          <label htmlFor="new-task-brief" className="text-text-secondary mb-1 block text-xs font-medium">
            {t('tasks.dialog.new.brief')}
          </label>
          <Textarea
            id="new-task-brief"
            value={brief}
            onChange={(e) => setBrief(e.target.value)}
            rows={4}
            placeholder={t('tasks.dialog.new.briefPlaceholder')}
          />
        </div>
        <div className="min-w-0">
          <label htmlFor="new-task-owner" className="text-text-secondary mb-1 block text-xs font-medium">
            {t('tasks.dialog.new.owner')}
          </label>
          <Select
            value={owner === '' ? DEFAULT_OWNER : owner}
            onValueChange={(next) => setOwner(next === DEFAULT_OWNER ? '' : next)}
          >
            <SelectTrigger id="new-task-owner" className="h-9 w-full">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value={DEFAULT_OWNER}>{t('tasks.dialog.new.ownerDefault')}</SelectItem>
              {(agents.data?.agents ?? []).map((agent) => (
                <SelectItem key={agent.name} value={agent.name}>
                  {agent.addressable_name && agent.addressable_name !== agent.name
                    ? `${agent.addressable_name} · ${agent.name}` : agent.name}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>
        <TaskSelectionFields
          idPrefix="new-task"
          options={options}
          draft={draft}
          onChange={setDraft}
          disabled={submit.isPending}
        />
        {refusal !== null && <TaskSelectionRefusal refusal={refusal} />}
        {error !== null && <p role="alert" className="text-feedback-danger text-sm">{renderTaskError(error, t)}</p>}
        <DialogFooter>
          <Button variant="ghost" onClick={onClose}>{t('common.cancel')}</Button>
          <Button disabled={submit.isPending} onClick={onSubmit}>
            {submit.isPending ? t('tasks.dialog.new.pending') : t('tasks.dialog.new.confirm')}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
