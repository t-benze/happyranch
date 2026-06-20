/**
 * Task detail — Direction-A Pasture, drawer pane.
 *
 * Shows the task identity card (Pasture card style), the chain timeline
 * (current/done/blocked nodes, blocked node names its blocker), the brief,
 * execution subtasks (from recall), live events, and jobs cross-link.
 *
 * Uses ds.css .card styling (bg-surface, rounded-lg, shadow-pasture-sm).
 * Title in serif font-display.
 */
import { useState } from 'react';
import { Link, useNavigate, useParams } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import {
  Drawer,
  DrawerContent,
  DrawerTitle,
} from '@/design-system/primitives/Drawer';
import { Button } from '@/design-system/primitives/Button';
import { IdBadge } from '@/design-system/patterns/IdBadge';
import { StatusBadge } from '@/design-system/patterns/StatusBadge';
import { Markdown } from '@/design-system/patterns/Markdown';
import { useTask, useTaskRecall, useTasksRoutes } from '@/hooks/tasks';
import { useJobsList } from '@/hooks/jobs';
// eslint-disable-next-line no-restricted-imports -- no @/hooks accessor exposes getTask (useTask deliberately drops active_chain); routed direct per THR-011 founder ruling (option 3), pending a future hook
import { getTask } from '@/lib/api/tasks';
import type { ActiveChainResponse, TaskRecallNode } from '@/lib/api/types';
import { TaskRecallTree } from './TaskRecallTree';
import { TaskEventsLog } from './TaskEventsLog';
import { CancelTaskDialog } from './CancelTaskDialog';
import { RevisitTaskDialog } from './RevisitTaskDialog';
import { ResolveEscalationDialog } from './ResolveEscalationDialog';

/* ================================================================
 * Timeline node — ds.css chain styling for the workflow strip
 * ================================================================ */

/** A single node in the chain timeline. */
interface TimelineNodeProps {
  label: string;
  detail?: string;
  state: 'done' | 'current' | 'blocked' | 'pending';
  blockerName?: string;
}

function dotColor(state: TimelineNodeProps['state']): string {
  switch (state) {
    case 'done':
      return 'bg-status-open';
    case 'current':
      return 'bg-accent-default';
    case 'blocked':
      return 'bg-status-escalated';
    case 'pending':
      return 'bg-border-strong';
  }
}

function dotRing(state: TimelineNodeProps['state']): string {
  switch (state) {
    case 'done':
    case 'current':
    case 'blocked':
      return 'ring-2 ring-offset-1 ring-offset-surface';
    case 'pending':
      return '';
  }
}

function textClass(state: TimelineNodeProps['state']): string {
  switch (state) {
    case 'done':
      return 'text-text-secondary';
    case 'current':
      return 'text-text-primary font-semibold';
    case 'blocked':
      return 'text-status-escalated font-semibold';
    case 'pending':
      return 'text-text-muted';
  }
}

function TimelineNodeItem({
  label,
  detail,
  state,
  blockerName,
}: TimelineNodeProps): JSX.Element {
  return (
    <li className="flex gap-3">
      {/* Vertical connector */}
      <div className="flex flex-col items-center">
        <div
          className={`mt-1 h-2.5 w-2.5 shrink-0 rounded-full ${dotColor(state)} ${dotRing(state)}`}
        />
      </div>
      <div className="min-w-0 flex-1 pb-3">
        <span className={`text-sm ${textClass(state)}`}>{label}</span>
        {detail && (
          <span className="text-text-muted ml-2 font-mono text-xs">{detail}</span>
        )}
        {state === 'blocked' && blockerName && (
          <p className="text-status-escalated mt-0.5 text-xs">
            Blocked on: <span className="font-mono">{blockerName}</span>
          </p>
        )}
      </div>
    </li>
  );
}

/* ================================================================
 * Chain timeline
 * ================================================================ */

function WorkflowChainTimeline({
  chain,
}: {
  chain: ActiveChainResponse;
}): JSX.Element {
  const totalLegs = 1 + chain.legs.length;
  const currentIdx = chain.step_index;

  return (
    <section className="mt-5">
      <h3 className="text-text-secondary mb-3 text-xs font-semibold tracking-wider uppercase">
        Workflow chain — step {currentIdx + 1} of {totalLegs}
      </h3>
      <ol>
        {/* First leg */}
        <TimelineNodeItem
          label="Leg 1 (first leg)"
          detail={chain.first_leg_expect_verdict ?? undefined}
          state={currentIdx === 0 ? 'current' : currentIdx > 0 ? 'done' : 'pending'}
        />
        {/* Subsequent legs */}
        {chain.legs.map((leg, i) => {
          const legNum = i + 2;
          const legState: TimelineNodeProps['state'] =
            currentIdx === legNum - 1
              ? 'current'
              : currentIdx >= legNum
                ? 'done'
                : 'pending';
          return (
            <TimelineNodeItem
              key={legNum}
              label={leg.agent}
              detail={leg.expect_verdict ?? undefined}
              state={legState}
            />
          );
        })}
      </ol>
    </section>
  );
}

/* ================================================================
 * Execution subtasks — pulled from recall tree
 * ================================================================ */

interface SubtaskRowProps {
  node: TaskRecallNode;
  depth?: number;
}

const DEPTH_PL = [
  'ml-0', 'ml-4', 'ml-8', 'ml-12', 'ml-16',
  'ml-20', 'ml-24', 'ml-28', 'ml-32',
] as const;

function SubtaskRow({ node, depth = 0 }: SubtaskRowProps): JSX.Element {
  const routes = useTasksRoutes();
  const ml = DEPTH_PL[Math.min(depth, DEPTH_PL.length - 1)];
  return (
    <div className={`${ml}`}>
      <div className="border-border-default flex items-center gap-2 border-l-2 py-1.5 pl-3 text-sm">
        <span
          className={`inline-block h-2 w-2 shrink-0 rounded-full ${
            node.status === 'completed'
              ? 'bg-status-open'
              : node.status === 'failed'
                ? 'bg-status-abandoned'
                : node.status === 'in_progress'
                  ? 'bg-accent-default'
                  : 'bg-border-strong'
          }`}
          aria-hidden
        />
        <IdBadge kind="task" id={node.task_id} to={routes.detail(node.task_id)} />
        <StatusBadge status={node.status} />
        {node.assigned_agent && (
          <span className="text-text-muted text-xs">{node.assigned_agent}</span>
        )}
      </div>
      {node.children.map((c) => (
        <SubtaskRow key={c.task_id} node={c} depth={depth + 1} />
      ))}
    </div>
  );
}

function ExecutionSubtasks({ recall }: { recall: TaskRecallNode }): JSX.Element {
  const hasSubtasks = recall.children && recall.children.length > 0;
  return (
    <section className="mt-5">
      <h3 className="text-text-secondary mb-2 text-xs font-semibold tracking-wider uppercase">
        Execution subtasks
      </h3>
      {!hasSubtasks ? (
        <p className="text-text-muted text-xs">No subtasks.</p>
      ) : (
        <div className="space-y-0">
          {recall.children.map((c) => (
            <SubtaskRow key={c.task_id} node={c} />
          ))}
        </div>
      )}
    </section>
  );
}

/* ================================================================
 * Brief section
 * ================================================================ */

const BRIEF_COLLAPSE_THRESHOLD = 600;

function BriefSection({ brief }: { brief: string }): JSX.Element {
  const [expanded, setExpanded] = useState(false);
  const shouldCollapse = brief.length > BRIEF_COLLAPSE_THRESHOLD;
  const preview =
    shouldCollapse && !expanded
      ? brief.slice(0, BRIEF_COLLAPSE_THRESHOLD).replace(/\s+\S*$/, '') + '…'
      : brief;

  return (
    <section className="mt-5">
      <h3 className="text-text-secondary mb-2 text-xs font-semibold tracking-wider uppercase">
        Brief
      </h3>
      <div className="border-border-default bg-surface-sunken rounded-md border p-3">
        <Markdown body={preview} />
        {shouldCollapse && (
          <button
            type="button"
            onClick={() => setExpanded((v) => !v)}
            className="text-accent-default mt-2 text-xs hover:underline"
          >
            {expanded
              ? 'Show less'
              : `Show full brief (${brief.length} chars)`}
          </button>
        )}
      </div>
    </section>
  );
}

/* ================================================================
 * Main pane
 * ================================================================ */

const TERMINAL_STATUSES: ReadonlySet<string> = new Set([
  'failed',
  'completed',
  'cancelled',
  'resolved_superseded',
]);

export function TaskDetailPane({ taskId }: { taskId: string }): JSX.Element {
  const navigate = useNavigate();
  const { slug } = useParams<{ slug: string }>();
  const routes = useTasksRoutes();
  const task = useTask(taskId);
  const recall = useTaskRecall(taskId);
  const jobsQuery = useJobsList({ task_id: taskId, status: 'all', limit: 100 });
  const activeChainQuery = useQuery({
    queryKey: ['task', slug, taskId],
    queryFn: () => getTask(slug as string, taskId),
    select: (r) => r.active_chain ?? null,
    enabled: !!slug && !!taskId,
  });
  const [dialog, setDialog] = useState<null | 'cancel' | 'revisit' | 'resolve'>(null);

  const onClose = () => navigate(routes.inbox());
  const isEscalated = task.data?.status === 'blocked' && task.data?.block_kind === 'escalated';
  const isTerminal = task.data ? TERMINAL_STATUSES.has(task.data.status) : false;
  const isFailed = task.data?.status === 'failed';
  const note = task.data ? (task.data as { note?: unknown }).note : undefined;
  const failureNote = isFailed && typeof note === 'string' && note ? note : null;
  const brief = task.data?.brief ?? '';

  return (
    <>
      <Drawer open onOpenChange={(o) => !o && onClose()}>
        <DrawerContent className="flex flex-col">
          {/* Header — identity card */}
          <header className="border-border-default shrink-0 border-b px-5 py-4">
            <DrawerTitle className="flex items-center gap-2">
              <span className="font-display text-h1 text-text-primary font-medium">
                {taskId}
              </span>
              {task.data && (
                <StatusBadge
                  status={task.data.status}
                  blockKind={task.data.block_kind}
                />
              )}
            </DrawerTitle>
            {task.data && (
              <div className="text-text-muted mt-2 flex flex-wrap items-center gap-x-2 gap-y-1 font-mono text-xs tabular-nums">
                <span>{task.data.team}</span>
                {task.data.assigned_agent && (
                  <span>· {task.data.assigned_agent}</span>
                )}
                {task.data.parent_task_id && (
                  <span>
                    · parent{' '}
                    <Link
                      to={routes.detail(task.data.parent_task_id)}
                      className="text-id-task hover:underline"
                    >
                      {task.data.parent_task_id}
                    </Link>
                  </span>
                )}
                {task.data.revisit_of_task_id && (
                  <span>
                    · revisit of{' '}
                    <Link
                      to={routes.detail(task.data.revisit_of_task_id)}
                      className="text-id-task hover:underline"
                    >
                      {task.data.revisit_of_task_id}
                    </Link>
                  </span>
                )}
              </div>
            )}
            {failureNote && (
              <div
                role="alert"
                className="bg-tier-red-tint text-status-abandoned mt-3 max-h-32 overflow-y-auto rounded-md px-3 py-2 text-sm"
              >
                <span className="font-semibold">Failure reason:</span>{' '}
                <span className="font-mono">{failureNote}</span>
              </div>
            )}
            <div className="mt-3 flex gap-2">
              {isEscalated && (
                <Button size="sm" onClick={() => setDialog('resolve')}>
                  Resolve…
                </Button>
              )}
              <Button size="sm" variant="ghost" onClick={() => setDialog('revisit')}>
                Revisit
              </Button>
              <Button
                size="sm"
                variant="ghost"
                onClick={() => setDialog('cancel')}
                disabled={isTerminal}
                title={
                  isTerminal
                    ? `Cannot cancel a ${task.data?.status} task`
                    : undefined
                }
              >
                Cancel
              </Button>
              {slug && (
                <Link
                  to={`/orgs/${slug}/audit?task_id=${taskId}`}
                  className="text-accent-default ml-auto self-center text-xs hover:underline"
                >
                  View audit →
                </Link>
              )}
            </div>
          </header>

          {/* Scrollable body */}
          <section className="min-h-0 flex-1 overflow-y-auto px-5 py-4">
            {task.data?.brief && <BriefSection brief={brief} />}

            {activeChainQuery.data && (
              <WorkflowChainTimeline chain={activeChainQuery.data} />
            )}

            {recall.data && <ExecutionSubtasks recall={recall.data} />}

            <section className="mt-5">
              <h3 className="text-text-secondary mb-2 text-xs font-semibold tracking-wider uppercase">
                Recall tree
              </h3>
              {recall.data ? (
                <TaskRecallTree node={recall.data} />
              ) : (
                <p className="text-text-muted text-xs">Loading recall…</p>
              )}
            </section>

            <section className="mt-5">
              <h3 className="text-text-secondary mb-2 text-xs font-semibold tracking-wider uppercase">
                Live events
              </h3>
              <TaskEventsLog taskId={taskId} />
            </section>

            {jobsQuery.data && jobsQuery.data.jobs.length > 0 && (
              <section className="mt-5">
                <h3 className="text-text-secondary mb-2 text-xs font-semibold tracking-wider uppercase">
                  Jobs from this task
                </h3>
                <ul className="space-y-1 text-sm">
                  {jobsQuery.data.jobs.map((j) => (
                    <li key={j.id}>
                      {slug ? (
                        <Link
                          to={`/orgs/${slug}/jobs/${j.id}`}
                          className="text-accent-default font-mono hover:underline"
                        >
                          {j.id}
                        </Link>
                      ) : (
                        <span className="font-mono">{j.id}</span>
                      )}
                      {' — '}
                      {j.title}{' '}
                      <span className="text-text-muted">({j.status})</span>
                    </li>
                  ))}
                </ul>
              </section>
            )}
          </section>
        </DrawerContent>
      </Drawer>

      {dialog === 'cancel' && (
        <CancelTaskDialog taskId={taskId} onClose={() => setDialog(null)} />
      )}
      {dialog === 'revisit' && (
        <RevisitTaskDialog taskId={taskId} onClose={() => setDialog(null)} />
      )}
      {dialog === 'resolve' && (
        <ResolveEscalationDialog taskId={taskId} onClose={() => setDialog(null)} />
      )}
    </>
  );
}
