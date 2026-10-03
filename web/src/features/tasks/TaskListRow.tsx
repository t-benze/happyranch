/**
 * TaskListRow — Direction-A Pasture single-line, aligned-column root-task row
 * (THR-030 TASKS-01 / TASKS-02, THR-046 msg-11). Flat columns: STATUS
 * (StatusBadge) · TASK (task_id IdBadge monospace) · TITLE · AGENT
 * (AgentChip) · THREAD (IdBadge) · UPDATED (relative age).
 *
 * Presentation-only over already-loaded /tasks/roots fields. Missing agent or
 * thread render a neutral em-dash — never a fabricated identity.
 *
 * TASKS-05 (worst-child rollup): when the root's severity_rollup is strictly
 * worse than its own status, a descendant sits in that worse state, so the row
 * names it inline ("subtask blocked"). This is count-free on purpose — the
 * design's count-decorated form ("1 of 2 subtasks blocked") needs per-status
 * subtask counts the /tasks/roots payload does not carry (it exposes only the
 * collapsed worst-status string), so the count layer is deferred, not faked.
 *
 * Local to the tasks feature on purpose: the shared TaskCard pattern stays
 * untouched (still consumed by features/agents/). Small helpers are duplicated
 * rather than reaching into TaskCard's private internals.
 */
import { Link } from 'react-router-dom';
import { StatusBadge } from '@/design-system/patterns/StatusBadge';
import { AgentChip } from '@/design-system/patterns/AgentChip';
import { IdBadge } from '@/design-system/patterns/IdBadge';
import type { TaskRecord, TaskStatus } from '@/lib/api/types';
import { useTranslation } from '@/hooks/i18n';
import type { MessageKey, MessageParams } from '@/lib/i18n';

type Translate = (key: MessageKey, params?: MessageParams) => string;

/** Route helper injected by the feature caller (keeps the row hook-free). */
export interface TaskListRoutes {
  detail(taskId: string): string;
}

/** The named Tasks grid is shared by the header and every desktop row. */
const COL = {
  status: 'min-w-0', task: 'min-w-0', title: 'min-w-0',
  agent: 'min-w-0', thread: 'min-w-0', updated: 'min-w-0 text-right',
} as const;
const ROW_FLEX = 'tasks-grid items-center';

function relativeAge(iso: string, t: Translate): string {
  const ms = Date.now() - new Date(iso).getTime();
  const min = Math.round(ms / 60000);
  if (min < 1) return t('tasks.age.justNow');
  if (min < 60) return t('tasks.age.minutes', { count: min });
  const hr = Math.round(min / 60);
  if (hr < 24) return t('tasks.age.hours', { count: hr });
  const d = Math.round(hr / 24);
  return t('tasks.age.days', { count: d });
}

function briefHeadline(brief: string): string {
  const line = brief.split('\n').find((l) => l.trim().length > 0) ?? '';
  return line.trim().replace(/^#+\s*/, '');
}

/** roots payload carries severity_rollup (worst subtree status); fall back to
 *  the root's own status when absent. */
export function severityRollupStatus(task: TaskRecord): TaskStatus {
  const r = (task as Record<string, unknown>).severity_rollup;
  if (typeof r === 'string' && r.length > 0) return r as TaskStatus;
  return task.status;
}

/**
 * Status text-color token for the inline subtask rollup. Mirrors StatusBadge's
 * .tag color mapping (kept local — the row deliberately does not reach into
 * StatusBadge internals). Drives both the led dot and the label color.
 */
const ROLLUP_COLOR: Record<TaskStatus, string> = {
  pending: 'text-status-archiving',
  in_progress: 'text-info',
  escalated: 'text-attention-text',
  blocked: 'text-status-escalated',
  completed: 'text-status-open',
  failed: 'text-status-abandoned',
  cancelled: 'text-status-archived',
  superseded: 'text-status-archived',
};

/** Localized label for a KNOWN worst-child status; an unknown future status
 *  renders verbatim (API machine value). */
const ROLLUP_LABEL_KEY: Record<string, MessageKey> = {
  pending: 'tasks.rollup.pending',
  in_progress: 'tasks.rollup.inProgress',
  escalated: 'tasks.rollup.escalated',
  blocked: 'tasks.rollup.blocked',
  completed: 'tasks.rollup.completed',
  failed: 'tasks.rollup.failed',
  cancelled: 'tasks.rollup.cancelled',
  superseded: 'tasks.rollup.superseded',
};

function rollupLabel(status: TaskStatus, t: Translate): string {
  return Object.prototype.hasOwnProperty.call(ROLLUP_LABEL_KEY, status)
    ? t(ROLLUP_LABEL_KEY[status])
    : status;
}

/**
 * Inline worst-child rollup. _worst_subtree_status returns the lowest-rank
 * (worst) status among the root and its descendants, so a rollup that differs
 * from the root's own status always comes from a strictly-worse descendant —
 * honest to render "subtask <status>" with no count claim.
 *
 * THR-046 msg-11: this now lives in the TITLE column under the headline text.
 * The STATUS column is reserved for the StatusBadge only, and the rollup clips
 * inside the title column instead of forcing adjacent columns to move.
 */
function SubtaskRollup({ status }: { status: TaskStatus }): JSX.Element {
  const { t } = useTranslation();
  return (
    <span
      className={`${ROLLUP_COLOR[status]} flex max-w-full items-center gap-1 overflow-hidden text-xs font-medium text-ellipsis whitespace-nowrap`}
    >
      <span className="inline-block h-1.5 w-1.5 rounded-full bg-current" aria-hidden />
      {t('tasks.row.subtaskRollup', { status: rollupLabel(status, t) })}
    </span>
  );
}

/**
 * Derived waiting qualifier for the TITLE second-line context. Mirrors
 * StatusBadge's waitingQualifier logic but kept local — the row deliberately
 * does not reach into StatusBadge internals. Returns null when there is
 * nothing to name (not in_progress, or no block_kind).
 */
function waitingContext(
  status: TaskStatus,
  blockKind: TaskRecord['block_kind'],
  t: Translate,
): string | null {
  if (status !== 'in_progress' || !blockKind) return null;
  if (blockKind === 'delegated') return t('tasks.waiting.subtasks');
  if (blockKind === 'blocked_on_job') return t('tasks.waiting.jobs');
  return null;
}

function directRevisits(task: TaskRecord): string[] {
  const r = (task as Record<string, unknown>).direct_revisits;
  if (Array.isArray(r)) return r.filter((v): v is string => typeof v === 'string');
  return [];
}

/** Thread reference the roots payload already carries (used for group-by). */
function threadRef(task: TaskRecord): string | null {
  const t = (task as Record<string, unknown>).dispatched_from_thread_id;
  return typeof t === 'string' && t.length > 0 ? t : null;
}

/**
 * Role for the agent avatar chip. Mirrors the threads surface's honest mapping
 * (THREADDET-02 participantChipRole): only the founder is distinguishable from
 * a bare name string, so every other agent renders as a worker — the dot is
 * decorative.
 */
function agentChipRole(name: string): 'worker' | 'founder' {
  return name === 'founder' ? 'founder' : 'worker';
}

/**
 * Aligned, muted, uppercase column-label row (TASKS-01, THR-046 msg-11).
 * STATUS and TASK are now separate columns; the bar is rounded.
 */
export function TaskListColumnHeader(): JSX.Element {
  const { t } = useTranslation();
  return (
    <div
      className={`${ROW_FLEX} text-text-muted border-border-default bg-surface-page tasks-column-header rounded-xl border shadow-sm font-semibold tracking-wide`}
    >
      <div className={COL.status}>{t('tasks.column.status')}</div>
      <div className={COL.task}>{t('tasks.column.task')}</div>
      <div className={COL.title}>{t('tasks.column.title')}</div>
      <div className={COL.agent}>{t('tasks.column.agent')}</div>
      <div className={COL.thread}>{t('tasks.column.thread')}</div>
      <div className={COL.updated}>{t('tasks.column.updated')}</div>
    </div>
  );
}

export interface TaskListRowProps {
  task: TaskRecord;
  to: string;
  taskRoutes: TaskListRoutes;
}

export function TaskListRow({ task, to, taskRoutes }: TaskListRowProps): JSX.Element {
  const { t, render } = useTranslation();
  const rollup = severityRollupStatus(task);
  const waiting = waitingContext(task.status, task.block_kind, t);
  const agent = task.assigned_agent;
  const thread = threadRef(task);
  const revisits = directRevisits(task);

  return (
    <div className={`border-border-default border-b ${task.status === 'superseded' ? 'opacity-60' : ''}`}>
      <Link
        to={to}
        className={`${ROW_FLEX} hover:bg-surface-hover tasks-row rounded-md text-sm no-underline transition-colors`}
      >
        {/* STATUS — compact primary task status only, whitespace-nowrap to prevent wrapping */}
        <div className={`${COL.status} whitespace-nowrap`}>
          <StatusBadge status={task.status} presentation="tasks" />
        </div>
        {/* TASK — monospace task-id badge */}
        <div className={`${COL.task} tasks-id truncate [&>span]:text-text-muted`} title={task.task_id}>
          <IdBadge id={task.task_id} kind="task" />
        </div>
        {/* TITLE — headline text + optional second-line context (waiting qualifier
            and/or worst-child severity rollup), both clipped to the title column */}
        <div className={`${COL.title} flex flex-col items-start justify-center gap-0.5 overflow-hidden`}>
          <span className="text-text-primary text-task-title w-full min-w-0 truncate font-semibold" title={task.brief}>{briefHeadline(task.brief)}</span>
          {(waiting || rollup !== task.status) && (
            <div className="flex max-w-full items-center gap-1.5 overflow-hidden text-xs whitespace-nowrap">
              {waiting && (
                <span className="text-text-muted truncate">
                  {waiting}
                </span>
              )}
              {waiting && rollup !== task.status && (
                <span className="text-border-default" aria-hidden>
                  ·
                </span>
              )}
              {rollup !== task.status && <SubtaskRollup status={rollup} />}
            </div>
          )}
        </div>
        {/* AGENT — AgentChip or em-dash fallback */}
        <div className={`${COL.agent} truncate`} title={agent ?? undefined}>
          {agent ? (
            <AgentChip name={agent} role={agentChipRole(agent)} />
          ) : (
            <span className="text-text-muted">—</span>
          )}
        </div>
        {/* THREAD — IdBadge or em-dash fallback */}
        <div className={`${COL.thread} tasks-id truncate`} title={thread ?? undefined}>
          {thread ? (
            <span className="[&>span]:text-info"><IdBadge id={thread} kind="thread" /></span>
          ) : (
            <span className="text-text-muted">—</span>
          )}
        </div>
        {/* UPDATED — relative age */}
        <div className={`${COL.updated} text-task-meta font-mono text-text-muted whitespace-nowrap tabular-nums`}>
          {relativeAge(task.updated_at, t)}
        </div>
      </Link>

      {/* Supersede / revisit lineage — siblings of the row Link (no nested
          anchors), preserving the navigation TaskCard previously offered. */}
      {(task.revisit_of_task_id || revisits.length > 0) && (
        <div className="text-text-muted flex flex-wrap gap-x-3 gap-y-0.5 px-2 pb-1.5 text-xs">
          {task.revisit_of_task_id && (
            <Link to={taskRoutes.detail(task.revisit_of_task_id)} className="hover:underline">
              {render('tasks.row.supersedes', {
                id: <span className="text-id-task font-mono">{task.revisit_of_task_id}</span>,
              })}
            </Link>
          )}
          {revisits.map((rid) => (
            <Link key={rid} to={taskRoutes.detail(rid)} className="hover:underline">
              {render('tasks.row.supersededBy', {
                id: <span className="text-id-task font-mono">{rid}</span>,
              })}
            </Link>
          ))}
        </div>
      )}
    </div>
  );
}
