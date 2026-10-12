import { useRef, useState } from 'react';
import { Link } from 'react-router-dom';
import { Button } from '@/design-system/primitives/Button';
import { StatusBadge } from '@/design-system/patterns/StatusBadge';
import { usePauseTask, useResumeTask } from '@/hooks/tasks';
import { useOrgSlugOptional } from '@/lib/orgSlug';
import { useTranslation } from '@/hooks/i18n';
import { formatDateShapeFor } from '@/lib/i18n/format';
import type { MessageKey } from '@/lib/i18n';
import type { TaskPauseProjection, TaskRecord } from '@/lib/api/types';
import { classifyTaskError, renderTaskError, type TaskErrorView } from './strings';
import type { TaskListRoutes } from './TaskListRow';

const TERMINAL = new Set(['completed', 'failed', 'cancelled', 'superseded']);
const BLOCKER_KEYS: Record<string, MessageKey> = {
  task_session: 'tasks.pause.blocker.task', job: 'tasks.pause.blocker.job',
  launch: 'tasks.pause.blocker.launch', result_processing: 'tasks.pause.blocker.result',
  unknown: 'tasks.pause.blocker.unknown',
};
const STATE_KEYS: Record<TaskPauseProjection['control_state'], MessageKey> = {
  unheld: 'tasks.pause.unheld', pausing: 'tasks.pause.pausing', paused: 'tasks.pause.paused',
};

/** Shared production presentation for record and overview evidence. Raw owner/reason/IDs stay verbatim. */
export function TaskPauseEvidence({ pause, compact = false }: { pause: TaskPauseProjection; compact?: boolean }): JSX.Element {
  const { t, locale } = useTranslation();
  return <div className="text-text-secondary min-w-0 space-y-1 text-xs">
    <p>{t(pause.evidence_complete ? 'tasks.pause.evidenceComplete' : 'tasks.pause.evidenceIncomplete')}</p>
    {!compact && <p>{t('tasks.pause.observed', { stamp: formatDateShapeFor(locale, new Date(pause.observed_at), 'dateTime') })}</p>}
    {pause.blockers.length > 0 && <details>
      <summary className="text-text-primary cursor-pointer">{t('tasks.pause.blockers', { count: pause.blockers.length })}</summary>
      <ul className="mt-1 space-y-1">
        {pause.blockers.map((blocker, index) => <li key={index} className="break-words">
          <span className="font-semibold">{Object.hasOwn(BLOCKER_KEYS, blocker.kind) ? t(BLOCKER_KEYS[blocker.kind]) : blocker.kind}</span>
          {' · '}<span className="font-mono">{[blocker.task_id, blocker.job_id, blocker.owner_kind, blocker.reason].filter(Boolean).join(' · ')}</span>
          <span className="block">{t('tasks.pause.observed', { stamp: formatDateShapeFor(locale, new Date(blocker.observed_at), 'dateTime') })}</span>
        </li>)}
      </ul>
    </details>}
  </div>;
}

function PauseActions({ taskId, generation, held }: { taskId: string; generation: number; held: boolean }): JSX.Element {
  const { t } = useTranslation();
  const pause = usePauseTask(taskId);
  const resume = useResumeTask(taskId);
  const latch = useRef(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<TaskErrorView | null>(null);
  const submit = async () => {
    if (latch.current) return;
    latch.current = true;
    setBusy(true);
    setError(null);
    try {
      await (held ? resume : pause).mutateAsync({ expected_generation: generation });
    } catch (err) {
      setError(classifyTaskError(err, 'tasks.pause.error.failed'));
    } finally {
      latch.current = false;
      setBusy(false);
    }
  };
  return <div className="flex min-w-0 flex-wrap items-center gap-2">
    <Button size="sm" variant="outline" disabled={busy || pause.isPending || resume.isPending} onClick={() => void submit()}>
      {t(held ? 'tasks.pause.resume' : 'tasks.pause.pause')}
    </Button>
    {busy && <span role="status" className="text-text-muted text-xs">{t('tasks.pause.submitting')}</span>}
    {error && <p role="alert" className="text-status-abandoned min-w-0 break-words text-xs">{renderTaskError(error, t)}</p>}
  </div>;
}

export function TaskPauseControls({ task, routes, compact = false }: {
  task: Pick<TaskRecord, 'task_id' | 'parent_task_id' | 'status' | 'pause'>;
  routes: TaskListRoutes;
  compact?: boolean;
}): JSX.Element {
  const { t, render } = useTranslation();
  const org = useOrgSlugOptional();
  const pause = task.pause;
  const isRoot = task.parent_task_id === null;
  const sourceMatches = pause && pause.org_slug === org && (!isRoot || pause.root_task_id === task.task_id);
  const available = sourceMatches && Number.isSafeInteger(pause.generation) && pause.generation >= 0
    && Object.hasOwn(STATE_KEYS, pause.control_state);
  if (!sourceMatches || !pause) return <p className="text-text-muted text-xs">{t('tasks.pause.unavailable')}</p>;
  const terminal = TERMINAL.has(task.status);
  const mayAct = available && isRoot && !terminal && (pause.held || task.status === 'in_progress');
  return <div className="min-w-0 space-y-1" data-task-pause={task.task_id}>
    <div className="flex flex-wrap items-center gap-2 text-xs">
      <span className="text-text-primary font-semibold">{t(available ? STATE_KEYS[pause.control_state] : 'tasks.pause.unavailable')}</span>
      {!isRoot && <span className="text-text-secondary">{render(pause.effective_hold ? 'tasks.pause.inherited' : 'tasks.pause.rootLink', {
        id: <Link to={routes.detail(pause.root_task_id)} className="text-id-task font-mono hover:underline">{pause.root_task_id}</Link>,
      })}</span>}
      {mayAct && <PauseActions key={`${pause.org_slug}/${task.task_id}`} taskId={task.task_id} generation={pause.generation} held={pause.held} />}
    </div>
    {terminal && (pause.blockers.length > 0 || !pause.evidence_complete) && <p className="text-text-secondary text-xs">{t('tasks.pause.terminalDrain')}</p>}
    <TaskPauseEvidence pause={pause} compact={compact} />
  </div>;
}

/** Read-only root observation: no TaskRecord/NULL parent is reconstructed from a projection. */
export function TaskPauseOverviewRoot({ pause, routes }: { pause: TaskPauseProjection; routes: TaskListRoutes }): JSX.Element {
  const { t } = useTranslation();
  const org = useOrgSlugOptional();
  const available = pause.org_slug === org && Number.isSafeInteger(pause.generation) && pause.generation >= 0 && Object.hasOwn(STATE_KEYS, pause.control_state);
  return <li className="border-border-default min-w-0 space-y-2 rounded-md border p-3">
    <div className="flex flex-wrap items-center gap-2">
      <Link to={routes.detail(pause.root_task_id)} className="text-id-task break-words font-mono text-sm hover:underline">{pause.root_task_id}</Link>
      <StatusBadge status={pause.lifecycle_status} />
    </div>
    <p className="text-text-primary text-xs font-semibold">{t(available ? STATE_KEYS[pause.control_state] : 'tasks.pause.unavailable')}</p>
    {TERMINAL.has(pause.lifecycle_status) && (pause.blockers.length > 0 || !pause.evidence_complete) && <p className="text-text-secondary text-xs">{t('tasks.pause.terminalDrain')}</p>}
    <TaskPauseEvidence pause={pause} />
  </li>;
}
