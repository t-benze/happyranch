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
import type { ActiveChainResponse, TaskDetailResponse } from '@/lib/api/types';
import { TaskRecallTree } from './TaskRecallTree';
import { TaskEventsLog } from './TaskEventsLog';
import { CancelTaskDialog } from './CancelTaskDialog';
import { RevisitTaskDialog } from './RevisitTaskDialog';
import { ResolveEscalationDialog } from './ResolveEscalationDialog';

function WorkflowChainStrip({ chain }: { chain: ActiveChainResponse }): JSX.Element {
  const totalLegs = 1 + chain.legs.length;
  const currentIdx = chain.step_index;

  return (
    <section className="mt-6">
      <h3 className="text-fg-muted mb-2 text-xs font-medium tracking-wider uppercase">
        Workflow chain — step {currentIdx + 1} of {totalLegs}
      </h3>
      <ol className="space-y-1 text-sm">
        <li className="flex gap-2 items-baseline">
          <span aria-hidden className="w-4 shrink-0 text-center">
            {currentIdx === 0 ? '▶' : '✓'}
          </span>
          <span className="text-fg-muted">Leg 1 (first leg)</span>
          {chain.first_leg_expect_verdict && (
            <span className="text-fg-muted">· expecting: {chain.first_leg_expect_verdict}</span>
          )}
        </li>
        {chain.legs.map((leg, i) => {
          const legNum = i + 2;
          const marker =
            currentIdx === legNum - 1 ? '▶' : currentIdx >= legNum ? '✓' : '⋯';
          return (
            <li key={legNum} className="flex gap-2 items-baseline">
              <span aria-hidden className="w-4 shrink-0 text-center">{marker}</span>
              <span className="font-mono text-fg">{leg.agent}</span>
              <span className="text-fg-muted truncate">{leg.prompt}</span>
              {leg.expect_verdict && (
                <span className="text-fg-muted shrink-0">· expecting: {leg.expect_verdict}</span>
              )}
            </li>
          );
        })}
      </ol>
    </section>
  );
}

// ---------------------------------------------------------------------------
// Revisit chain timeline
// ---------------------------------------------------------------------------

interface ChainNode {
  taskId: string;
  status: string;
  isCurrent: boolean;
}

function RevisitChainTimeline({
  chainIds,
  currentTaskId,
  slug,
}: {
  chainIds: string[];
  currentTaskId: string;
  slug: string;
}): JSX.Element | null {
  if (chainIds.length <= 1) return null;

  // The chain is [current, predecessor, ..., original] — reverse for timeline
  const nodes: ChainNode[] = chainIds.map((id) => ({
    taskId: id,
    status: id === currentTaskId ? 'current' : 'done',
    isCurrent: id === currentTaskId,
  }));

  return (
    <section className="mt-6">
      <h3 className="text-fg-muted mb-3 text-xs font-medium tracking-wider uppercase">
        Lineage
      </h3>
      <div className="relative pl-4">
        {nodes.reverse().map((node, i) => {
          const isLast = i === nodes.length - 1;
          const marker = node.isCurrent ? '●' : '○';
          const markerColor = node.isCurrent
            ? 'text-accent ring-accent/30 ring-4 ring-offset-2'
            : 'text-fg-muted';

          return (
            <div key={node.taskId} className="relative flex items-start gap-3 pb-4">
              {/* Vertical line */}
              {!isLast && (
                <div className="border-border-subtle absolute left-[9px] top-6 h-full w-px border-l" />
              )}
              {/* Node marker */}
              <span
                className={`relative z-10 mt-1 inline-flex h-4 w-4 shrink-0 items-center justify-center rounded-full text-xs ${markerColor}`}
                aria-hidden
              >
                {marker}
              </span>
              {/* Node content */}
              <div className="min-w-0 flex-1">
                {slug ? (
                  <Link
                    to={`/orgs/${slug}/tasks/${node.taskId}`}
                    className="text-accent hover:underline font-mono text-sm tab-focus"
                  >
                    {node.taskId}
                  </Link>
                ) : (
                  <span className="font-mono text-sm">{node.taskId}</span>
                )}
                {node.isCurrent && (
                  <span className="text-fg-muted ml-2 text-xs">(current)</span>
                )}
              </div>
            </div>
          );
        })}
      </div>
    </section>
  );
}

// ---------------------------------------------------------------------------
// Blocked-on display
// ---------------------------------------------------------------------------

function BlockedOnInfo({
  taskId,
  slug,
}: {
  taskId: string;
  slug: string;
}): JSX.Element | null {
  const detailQuery = useQuery({
    queryKey: ['task', slug, taskId, 'blocked'],
    queryFn: () => getTask(slug, taskId),
    select: (r: TaskDetailResponse) => ({
      status: r.task.status,
      blockKind: r.task.block_kind,
      blockedOnJobIds: (r as Record<string, unknown>).blocked_on_jobs as
        | Array<{ job_id: string; status: string }>
        | null
        | undefined,
    }),
    enabled: !!slug && !!taskId,
  });

  if (!detailQuery.data) return null;
  const { status, blockKind, blockedOnJobIds } = detailQuery.data;

  if (status !== 'blocked') return null;

  let blockerText = 'Blocked';
  if (blockKind === 'escalated') {
    blockerText = 'Escalated — awaiting founder';
  } else if (blockKind === 'delegated') {
    blockerText = 'Delegated — waiting on children';
  } else if (blockedOnJobIds && blockedOnJobIds.length > 0) {
    blockerText = `Waiting on job${blockedOnJobIds.length > 1 ? 's' : ''}: ${blockedOnJobIds.map((j) => j.job_id).join(', ')}`;
  }

  return (
    <div className="bg-tier-red-tint text-status-abandoned mt-3 rounded-sm px-3 py-2 text-sm">
      <span className="font-semibold">Blocked:</span> {blockerText}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Property rail
// ---------------------------------------------------------------------------

function PropertyRail({ task }: { task: Record<string, unknown> }): JSX.Element | null {
  const items: { label: string; value: string }[] = [];
  if (task.assigned_agent) {
    items.push({ label: 'Assignee', value: task.assigned_agent as string });
  }
  if (task.team) {
    items.push({ label: 'Team', value: task.team as string });
  }
  if (task.created_at) {
    items.push({ label: 'Created', value: new Date(task.created_at as string).toLocaleDateString() });
  }
  if ((task as Record<string, unknown>).dispatched_from_thread_id) {
    items.push({
      label: 'Thread',
      value: (task as Record<string, unknown>).dispatched_from_thread_id as string,
    });
  }

  if (items.length === 0) return null;

  return (
    <div className="border-border-subtle mt-4 rounded border p-3">
      <h3 className="text-fg-muted mb-2 text-xs font-medium tracking-wider uppercase">
        Properties
      </h3>
      <dl className="space-y-1 text-sm">
        {items.map((item) => (
          <div key={item.label} className="flex justify-between gap-2">
            <dt className="text-fg-muted">{item.label}</dt>
            <dd className="text-fg truncate font-mono text-xs">{item.value}</dd>
          </div>
        ))}
      </dl>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Main component
// ---------------------------------------------------------------------------

const BRIEF_COLLAPSE_THRESHOLD = 600;

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
  // Re-uses the same queryKey as useTask so TanStack Query deduplicates the
  // fetch; this select picks the active_chain envelope field that useTask drops.
  const activeChainQuery = useQuery({
    queryKey: ['task', slug, taskId],
    queryFn: () => getTask(slug as string, taskId),
    select: (r) => r.active_chain ?? null,
    enabled: !!slug && !!taskId,
  });
  // Full detail response for chain + blocked_on data
  const detailQuery = useQuery({
    queryKey: ['task-detail', slug, taskId],
    queryFn: () => getTask(slug as string, taskId),
    enabled: !!slug && !!taskId,
  });

  const [dialog, setDialog] = useState<null | 'cancel' | 'revisit' | 'resolve'>(null);
  const [briefExpanded, setBriefExpanded] = useState(false);

  const onClose = () => navigate(routes.inbox());
  const isEscalated = task.data?.status === 'blocked' && task.data?.block_kind === 'escalated';
  const isTerminal = task.data ? TERMINAL_STATUSES.has(task.data.status) : false;
  const isFailed = task.data?.status === 'failed';
  const note = task.data ? (task.data as { note?: unknown }).note : undefined;
  const failureNote = isFailed && typeof note === 'string' && note ? note : null;
  const brief = task.data?.brief ?? '';
  const briefShouldCollapse = brief.length > BRIEF_COLLAPSE_THRESHOLD;
  const briefPreview =
    briefShouldCollapse && !briefExpanded
      ? brief.slice(0, BRIEF_COLLAPSE_THRESHOLD).replace(/\s+\S*$/, '') + '…'
      : brief;

  const revisitChain = (detailQuery.data?.revisit_chain as string[]) ?? [];

  return (
    <>
      <Drawer open onOpenChange={(o) => !o && onClose()}>
        <DrawerContent className="flex flex-col">
          <header className="border-border-subtle shrink-0 border-b p-4">
            <DrawerTitle className="text-fg flex items-center gap-2 text-lg">
              <IdBadge kind="task" id={taskId} />
              {task.data && <StatusBadge status={task.data.status} blockKind={task.data.block_kind} />}
            </DrawerTitle>
            {task.data && (
              <p className="text-fg-muted mt-2 flex flex-wrap items-center gap-x-2 gap-y-1 text-xs">
                <span>{task.data.team}</span>
                {task.data.assigned_agent && (
                  <span>· {task.data.assigned_agent}</span>
                )}
              </p>
            )}
            {failureNote && (
              <div
                role="alert"
                className="bg-tier-red-tint text-status-abandoned mt-3 max-h-32 overflow-y-auto rounded-sm px-3 py-2 text-sm"
              >
                <span className="font-semibold">Failure reason:</span>{' '}
                <span className="font-mono">{failureNote}</span>
              </div>
            )}
            {/* Blocked info */}
            {task.data?.status === 'blocked' && slug && (
              <BlockedOnInfo taskId={taskId} slug={slug} />
            )}
            <div className="mt-3 flex gap-2">
              {isEscalated && (
                <Button size="sm" onClick={() => setDialog('resolve')}>Resolve…</Button>
              )}
              <Button size="sm" variant="ghost" onClick={() => setDialog('revisit')}>
                Revisit
              </Button>
              <Button
                size="sm"
                variant="ghost"
                onClick={() => setDialog('cancel')}
                disabled={isTerminal}
                title={isTerminal ? `Cannot cancel a ${task.data?.status} task` : undefined}
              >
                Cancel
              </Button>
              {slug && (
                <Link
                  to={`/orgs/${slug}/audit?task_id=${taskId}`}
                  className="text-accent ml-auto self-center text-xs hover:underline"
                >
                  View audit →
                </Link>
              )}
            </div>
          </header>
          <section className="min-h-0 flex-1 overflow-y-auto p-4">
            {task.data && (
              <>
                {/* Property rail */}
                <PropertyRail task={task.data as unknown as Record<string, unknown>} />

                {/* Revisit chain timeline */}
                {slug && revisitChain.length > 0 && (
                  <RevisitChainTimeline
                    chainIds={revisitChain}
                    currentTaskId={taskId}
                    slug={slug}
                  />
                )}

                <h3 className="text-fg-muted mt-6 mb-2 text-xs font-medium tracking-wider uppercase">
                  Brief
                </h3>
                <Markdown body={briefPreview} />
                {briefShouldCollapse && (
                  <button
                    type="button"
                    onClick={() => setBriefExpanded((v) => !v)}
                    className="text-accent mt-2 text-xs hover:underline"
                  >
                    {briefExpanded ? 'Show less' : `Show full brief (${brief.length} chars)`}
                  </button>
                )}
              </>
            )}
            {activeChainQuery.data && (
              <WorkflowChainStrip chain={activeChainQuery.data} />
            )}
            <h3 className="text-fg-muted mt-6 mb-2 text-xs font-medium tracking-wider uppercase">
              Recall tree
            </h3>
            {recall.data ? (
              <TaskRecallTree node={recall.data} />
            ) : (
              <p className="text-fg-muted text-xs">Loading recall…</p>
            )}
            <h3 className="text-fg-muted mt-6 mb-2 text-xs font-medium tracking-wider uppercase">
              Live events
            </h3>
            <TaskEventsLog taskId={taskId} />
            {jobsQuery.data && jobsQuery.data.jobs.length > 0 && (
              <section className="mt-6">
                <h3 className="text-fg-muted mb-2 text-xs font-medium tracking-wider uppercase">
                  Jobs from this task
                </h3>
                <ul className="space-y-1 text-sm">
                  {jobsQuery.data.jobs.map((j) => (
                    <li key={j.id}>
                      {slug ? (
                        <Link
                          to={`/orgs/${slug}/jobs/${j.id}`}
                          className="text-accent hover:underline font-mono"
                        >
                          {j.id}
                        </Link>
                      ) : (
                        <span className="font-mono">{j.id}</span>
                      )}
                      {' — '}
                      {j.title}{' '}
                      <span className="text-fg-muted">({j.status})</span>
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
