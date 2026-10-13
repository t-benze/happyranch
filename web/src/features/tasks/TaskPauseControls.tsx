import { useRef, useState, type ReactNode } from 'react';
import { Link } from 'react-router-dom';
import { Pause, Play } from 'lucide-react';
import { Button } from '@/design-system/primitives/Button';
import { usePauseTask, useResumeTask } from '@/hooks/tasks';
import { useOrgSlugOptional } from '@/lib/orgSlug';
import { useTranslation } from '@/hooks/i18n';
import { formatDateShapeFor } from '@/lib/i18n/format';
import type { MessageKey } from '@/lib/i18n';
import type { TaskPauseProjection, TaskRecord } from '@/lib/api/types';
import { cn } from '@/lib/utils';
import { classifyTaskError, renderTaskError, type TaskErrorView } from './strings';
import type { TaskListRoutes } from './TaskListRow';

const TERMINAL = new Set(['completed', 'failed', 'cancelled', 'superseded']);
const BLOCKER_KEYS: Record<string, MessageKey> = {
  task_session: 'tasks.pause.blocker.task', job: 'tasks.pause.blocker.job',
  launch: 'tasks.pause.blocker.launch', result_processing: 'tasks.pause.blocker.result',
  unknown: 'tasks.pause.blocker.unknown',
};
// Only a live task session or job is "running"; launch/result-processing/unknown are never counted as such.
const RUNNING = new Set(['task_session', 'job']);
const STATES = new Set<TaskPauseProjection['control_state']>(['unheld', 'pausing', 'paused']);
const PILL = 'inline-flex min-w-0 items-center gap-2 rounded-pill border px-3 py-button-sm-block text-xs font-semibold';

/** Hold indicator beside the ordinary lifecycle pill: amber while pausing, neutral once paused. */
function HoldIndicator({ state }: { state: 'pausing' | 'paused' }): JSX.Element {
  const { t } = useTranslation();
  return state === 'pausing'
    ? <span className={cn(PILL, 'bg-attention-soft text-attention-text border-attention-soft')}>
      <span aria-hidden="true" className="bg-attention size-2 shrink-0 rounded-pill motion-safe:animate-pulse" />
      {t('tasks.pause.pausing')}
    </span>
    : <span className={cn(PILL, 'border-input bg-bg-subtle text-text-primary')}>
      <Pause aria-hidden="true" fill="currentColor" className="size-4 shrink-0" />
      {t('tasks.pause.paused')}
    </span>;
}

/** Default-collapsed drain disclosure. Raw owner/reason/IDs stay verbatim; no fabricated title/agent/start time. */
function DrainDisclosure({ pause, org, routes, waiting }: {
  pause: TaskPauseProjection; org: string | null | undefined; routes: TaskListRoutes; waiting: boolean;
}): JSX.Element {
  const { t, locale } = useTranslation();
  const running = pause.blockers.filter(blocker => RUNNING.has(blocker.kind)).length;
  const other = pause.blockers.length - running;
  const summary = [
    running > 0 && t('tasks.pause.stillRunning', { count: running }),
    other > 0 && t('tasks.pause.otherWaiting', { count: other }),
    !pause.evidence_complete && t('tasks.pause.evidenceUnknown'),
  ].filter(Boolean).join(' · ');
  const taskLink = (id: string) => <Link to={routes.detail(id)} className="text-info font-mono hover:underline">{id}</Link>;
  return <details className="order-2 min-w-0 basis-full text-xs md:order-3">
    <summary className="text-text-primary focus-visible:ring-ring w-fit cursor-pointer rounded-sm py-1 font-semibold focus-visible:ring-2 focus-visible:outline-none">{summary}</summary>
    {pause.blockers.length > 0 && <ul className="border-border-default bg-bg-raised divide-border-default mt-1 min-w-0 divide-y rounded-md border md:max-w-content-form">
      {pause.blockers.map((blocker, index) => <li key={index} className="flex min-w-0 flex-wrap items-baseline gap-x-3 gap-y-1 px-3 py-2">
        {blocker.job_id && org
          ? <Link to={`/orgs/${org}/jobs/${blocker.job_id}`} className="text-info font-mono hover:underline">{blocker.job_id}</Link>
          : taskLink(blocker.task_id)}
        <span className="text-text-primary">{Object.hasOwn(BLOCKER_KEYS, blocker.kind) ? t(BLOCKER_KEYS[blocker.kind]) : blocker.kind}</span>
        <span className="text-text-muted ml-auto min-w-0 break-words font-mono">
          {blocker.job_id && org && <>{taskLink(blocker.task_id)}{' · '}</>}
          {[blocker.job_id && !org ? blocker.job_id : null, blocker.owner_kind, blocker.reason].filter(Boolean).join(' · ')}
        </span>
      </li>)}
    </ul>}
    <div className="text-text-muted mt-1 space-y-1">
      {waiting && <p>{t('tasks.pause.waitingNote')}</p>}
      {!pause.evidence_complete && <p>{t('tasks.pause.evidenceIncomplete')}</p>}
      <p>{t('tasks.pause.observed', { stamp: formatDateShapeFor(locale, new Date(pause.observed_at), 'dateTime') })}</p>
    </div>
  </details>;
}

/** Root pause region inside the shared header row. `contents` lets its parts take mobile/desktop order. */
function PauseRegion({ task, pause, routes, org, available, mayAct }: {
  task: Pick<TaskRecord, 'task_id' | 'parent_task_id' | 'status'>; pause: TaskPauseProjection; routes: TaskListRoutes;
  org: string | null | undefined; available: boolean; mayAct: boolean;
}): JSX.Element {
  const { t, render } = useTranslation();
  const pauseTask = usePauseTask(task.task_id);
  const resumeTask = useResumeTask(task.task_id);
  const latch = useRef(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<TaskErrorView | null>(null);
  const isRoot = task.parent_task_id === null;
  const terminal = TERMINAL.has(task.status);
  const held = pause.held;
  const submit = async () => {
    if (latch.current) return;
    latch.current = true;
    setBusy(true);
    setError(null);
    try {
      await (held ? resumeTask : pauseTask).mutateAsync({ expected_generation: pause.generation });
    } catch (err) {
      setError(classifyTaskError(err, 'tasks.pause.error.failed'));
    } finally {
      latch.current = false;
      setBusy(false);
    }
  };
  const holdState = available && !terminal && (isRoot ? held : pause.effective_hold) && pause.control_state !== 'unheld'
    ? pause.control_state as 'pausing' | 'paused' : null;
  const diagnostics = (terminal || held || pause.effective_hold) && (pause.blockers.length > 0 || !pause.evidence_complete);
  const action = mayAct && <Button
    key="action" size="sm" variant={held && pause.control_state === 'paused' ? 'default' : 'outline'}
    disabled={busy || pauseTask.isPending || resumeTask.isPending} onClick={() => void submit()}
  >
    {held ? <Play aria-hidden="true" fill="currentColor" /> : <Pause aria-hidden="true" fill="currentColor" />}
    {t(held ? 'tasks.pause.resume' : 'tasks.pause.pause')}
  </Button>;
  const group = [
    holdState && <HoldIndicator key="state" state={holdState} />,
    !available && <span key="unavailable" className="text-text-muted text-xs">{t('tasks.pause.unavailable')}</span>,
    !isRoot && <span key="root" className="text-text-secondary text-xs">{render(pause.effective_hold ? 'tasks.pause.inherited' : 'tasks.pause.rootLink', {
      id: <Link to={routes.detail(pause.root_task_id)} className="text-id-task font-mono hover:underline">{pause.root_task_id}</Link>,
    })}</span>,
    terminal && diagnostics && <span key="terminal" className="text-text-secondary text-xs">{t('tasks.pause.terminalDrain')}</span>,
    action,
    busy && <span key="busy" role="status" className="text-text-muted text-xs">{t('tasks.pause.submitting')}</span>,
  ].filter(Boolean);
  return <div className="contents" data-task-pause={task.task_id}>
    {group.length > 0 && <div className="order-1 flex min-w-0 flex-wrap items-center gap-2">{group}</div>}
    {diagnostics && <DrainDisclosure pause={pause} org={org} routes={routes} waiting={holdState === 'pausing' && isRoot} />}
    {error && <p role="alert" className="text-status-abandoned order-2 min-w-0 basis-full break-words text-xs md:order-3">{renderTaskError(error, t)}</p>}
  </div>;
}

/**
 * Task-detail header action row (THR-292 seq77): root hold indicator + Pause/Resume share the row
 * with the ordinary `actions` (Revisit/Cancel or Continue/Cancel and the audit link). Mobile order is
 * hold/Resume, drain disclosure, then ordinary actions; desktop keeps one row with the disclosure below.
 */
export function TaskPauseControls({ task, routes, actions }: {
  /** Absent while the detail read is loading: the row keeps the same structure so actions never remount. */
  task: Pick<TaskRecord, 'task_id' | 'parent_task_id' | 'status' | 'pause'> | undefined;
  routes: TaskListRoutes;
  actions: ReactNode;
}): JSX.Element {
  const { t } = useTranslation();
  const org = useOrgSlugOptional();
  const pause = task?.pause;
  const isRoot = task?.parent_task_id === null;
  const sourceMatches = !!task && !!pause && pause.org_slug === org && (!isRoot || pause.root_task_id === task.task_id);
  const available = sourceMatches && Number.isSafeInteger(pause.generation) && pause.generation >= 0
    && STATES.has(pause.control_state);
  const mayAct = available && isRoot && !TERMINAL.has(task.status) && (pause.held || task.status === 'in_progress');
  const separated = mayAct && pause.held;
  return <div className="mt-3 flex flex-wrap items-start gap-2">
    {!task ? null : sourceMatches
      ? <PauseRegion key={`${pause.org_slug}/${task.task_id}`} task={task} pause={pause} routes={routes} org={org} available={available} mayAct={mayAct} />
      : <p className="text-text-muted order-1 basis-full text-xs">{t('tasks.pause.unavailable')}</p>}
    <div className={cn('order-3 flex min-w-0 flex-wrap items-center gap-2 md:order-2', separated ? 'basis-full md:basis-auto md:flex-1' : 'flex-1')}>
      {separated && <span aria-hidden="true" className="border-border-default hidden h-5 border-l md:block" />}
      {actions}
    </div>
  </div>;
}
