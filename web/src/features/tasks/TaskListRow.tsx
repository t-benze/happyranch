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

/** Route helper injected by the feature caller (keeps the row hook-free). */
export interface TaskListRoutes {
  detail(taskId: string): string;
}

/**
 * Shared flex column widths. The header row and every data row use the same
 * tokens so the columns line up. Standard Tailwind widths only — the feature
 * surface forbids arbitrary values (`tailwindcss/no-arbitrary-value`).
 *
 * THR-046 msg-11: STATUS and TASK are now separate columns (the old TASK
 * column contained the status pill; now STATUS holds the pill and TASK holds
 * the monospace task-id badge).
 */
const COL = {
  status: 'w-28 shrink-0',
  task: 'w-24 shrink-0',
  title: 'min-w-0 flex-1',
  agent: 'w-36 shrink-0',
  thread: 'w-24 shrink-0',
  updated: 'w-14 shrink-0 text-right',
} as const;

const ROW_FLEX = 'flex items-center gap-3';

function relativeAge(iso: string): string {
  const ms = Date.now() - new Date(iso).getTime();
  const min = Math.round(ms / 60000);
  if (min < 1) return 'just now';
  if (min < 60) return `${min}m`;
  const hr = Math.round(min / 60);
  if (hr < 24) return `${hr}h`;
  const d = Math.round(hr / 24);
  return `${d}d`;
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
  in_progress: 'text-status-open',
  escalated: 'text-status-escalated',
  completed: 'text-status-open',
  failed: 'text-status-abandoned',
  cancelled: 'text-status-archived',
  resolved_superseded: 'text-status-archived',
};

/** Human label for the worst-child status (spaces, no underscores). */
function rollupLabel(status: TaskStatus): string {
  return status === 'resolved_superseded' ? 'superseded' : status.replace(/_/g, ' ');
}

/**
 * Inline worst-child rollup. _worst_subtree_status returns the lowest-rank
 * (worst) status among the root and its descendants, so a rollup that differs
 * from the root's own status always comes from a strictly-worse descendant —
 * honest to render "subtask <status>" with no count claim.
 *
 * THR-046 msg-11: this now lives in the TITLE column alongside the headline
 * text. The STATUS column is reserved for the StatusBadge only, so the rollup
 * no longer stacks on top of the badge.
 */
function SubtaskRollup({ status }: { status: TaskStatus }): JSX.Element {
  return (
    <span
      className={`${ROLLUP_COLOR[status]} inline-flex shrink-0 items-center gap-1 text-xs font-medium whitespace-nowrap`}
    >
      <span className="inline-block h-1.5 w-1.5 rounded-full bg-current" aria-hidden />
      subtask {rollupLabel(status)}
    </span>
  );
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
  return (
    <div
      className={`${ROW_FLEX} text-text-muted bg-surface-sunken rounded-lg px-3 py-2 text-xs font-medium tracking-wide`}
    >
      <div className={COL.status}>STATUS</div>
      <div className={COL.task}>TASK</div>
      <div className={COL.title}>TITLE</div>
      <div className={COL.agent}>AGENT</div>
      <div className={COL.thread}>THREAD</div>
      <div className={COL.updated}>UPDATED</div>
    </div>
  );
}

export interface TaskListRowProps {
  task: TaskRecord;
  to: string;
  taskRoutes: TaskListRoutes;
}

export function TaskListRow({ task, to, taskRoutes }: TaskListRowProps): JSX.Element {
  const rollup = severityRollupStatus(task);
  const agent = task.assigned_agent;
  const thread = threadRef(task);
  const revisits = directRevisits(task);

  return (
    <div className="border-border-default border-b last:border-b-0">
      <Link
        to={to}
        className={`${ROW_FLEX} hover:bg-surface-hover rounded-md px-2 py-2.5 text-sm no-underline transition-colors`}
      >
        {/* STATUS — StatusBadge only, whitespace-nowrap to prevent wrapping */}
        <div className={`${COL.status} whitespace-nowrap`}>
          <StatusBadge status={rollup} blockKind={task.block_kind} />
        </div>
        {/* TASK — monospace task-id badge */}
        <div className={`${COL.task} whitespace-nowrap`}>
          <IdBadge id={task.task_id} kind="task" />
        </div>
        {/* TITLE — headline text + inline subtask rollup */}
        <div className={`${COL.title} flex items-center gap-2 overflow-hidden`}>
          <span className="text-text-primary truncate">{briefHeadline(task.brief)}</span>
          {rollup !== task.status && <SubtaskRollup status={rollup} />}
        </div>
        {/* AGENT — AgentChip or em-dash fallback */}
        <div className={`${COL.agent} truncate`}>
          {agent ? (
            <AgentChip name={agent} role={agentChipRole(agent)} />
          ) : (
            <span className="text-text-muted">—</span>
          )}
        </div>
        {/* THREAD — IdBadge or em-dash fallback */}
        <div className={`${COL.thread} truncate`}>
          {thread ? (
            <IdBadge id={thread} kind="thread" />
          ) : (
            <span className="text-text-muted">—</span>
          )}
        </div>
        {/* UPDATED — relative age */}
        <div className={`${COL.updated} text-text-muted tabular-nums`}>
          {relativeAge(task.updated_at)}
        </div>
      </Link>

      {/* Supersede / revisit lineage — siblings of the row Link (no nested
          anchors), preserving the navigation TaskCard previously offered. */}
      {(task.revisit_of_task_id || revisits.length > 0) && (
        <div className="text-text-muted flex flex-wrap gap-x-3 gap-y-0.5 px-2 pb-1.5 text-xs">
          {task.revisit_of_task_id && (
            <Link to={taskRoutes.detail(task.revisit_of_task_id)} className="hover:underline">
              supersedes{' '}
              <span className="text-id-task font-mono">{task.revisit_of_task_id}</span>
            </Link>
          )}
          {revisits.map((rid) => (
            <Link key={rid} to={taskRoutes.detail(rid)} className="hover:underline">
              superseded by <span className="text-id-task font-mono">{rid}</span>
            </Link>
          ))}
        </div>
      )}
    </div>
  );
}
