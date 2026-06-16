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

// ── helpers ──

const BRIEF_COLLAPSE_THRESHOLD = 600;

const TERMINAL_STATUSES: ReadonlySet<string> = new Set([
  'failed',
  'completed',
  'cancelled',
  'resolved_superseded',
]);

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

// ── chain timeline ──

interface ChainNode {
  task_id: string;
  status: string;
  isCurrent: boolean;
  blockedOnJobs?: Array<{ job_id: string; status: string }> | null;
}

function RevisitChainTimeline({
  revisitChain,
  directRevisits,
}: {
  revisitChain: string[];
  directRevisits: string[];
}): JSX.Element | null {
  if (revisitChain.length <= 1 && directRevisits.length === 0) return null;

  // Build nodes: the revisit_chain goes from this task → predecessor → ... → original.
  // We render the chain left-to-right: original → ... → current.
  const nodes: ChainNode[] = revisitChain.map((id, i) => ({
    task_id: id,
    status: i === 0 ? 'current' : 'done',
    isCurrent: i === 0,
  }));
  // Add direct revisits (successor tasks)
  const successorNodes: ChainNode[] = directRevisits.map((id) => ({
    task_id: id,
    status: 'future',
    isCurrent: false,
  }));

  if (nodes.length <= 1 && successorNodes.length === 0) return null;

  const routes = useTasksRoutes();

  return (
    <section className="mt-6">
      <h3 className="text-fg-muted mb-3 text-xs font-medium tracking-wider uppercase">
        Revisit chain
      </h3>
      <div className="flex flex-wrap items-center gap-1.5">
        {/* predecessors (oldest first) */}
        {nodes.length > 1 &&
          nodes
            .slice()
            .reverse()
            .slice(0, -1)
            .map((node) => (
              <span key={node.task_id} className="inline-flex items-center gap-1">
                <Link
                  to={routes.detail(node.task_id)}
                  className="text-fg-muted hover:text-fg text-xs font-mono hover:underline"
                >
                  {node.task_id}
                </Link>
                <span className="text-fg-subtle text-xs">→</span>
              </span>
            ))}
        {/* current */}
        {nodes.length > 0 && (
          <span className="inline-flex items-center gap-1">
            <span className="ring-accent text-fg rounded-sm bg-accent-muted px-1.5 py-0.5 text-xs font-mono font-semibold ring-2">
              {nodes[0].task_id}
            </span>
          </span>
        )}
        {/* successors */}
        {successorNodes.map((node) => (
          <span key={node.task_id} className="inline-flex items-center gap-1">
            <span className="text-fg-subtle text-xs">→</span>
            <Link
              to={routes.detail(node.task_id)}
              className="text-fg-muted hover:text-fg text-xs font-mono hover:underline"
            >
              {node.task_id}
            </Link>
          </span>
        ))}
      </div>
    </section>
  );
}

// ── property rail ──

function PropertyRail({
  task,
  detailEnvelope,
}: {
  task: Record<string, unknown>;
  detailEnvelope: TaskDetailResponse | null;
}): JSX.Element {
  const slug = useParams<{ slug: string }>().slug;
  const props: Array<{ label: string; value: React.ReactNode }> = [];

  if (task.team) {
    props.push({ label: 'Team', value: String(task.team) });
  }
  if (task.assigned_agent) {
    props.push({ label: 'Assignee', value: String(task.assigned_agent) });
  }
  if (task.dispatched_from_thread_id) {
    const tid = String(task.dispatched_from_thread_id);
    props.push({
      label: 'Thread',
      value: slug ? (
        <Link to={`/orgs/${slug}/threads/${tid}`} className="text-accent hover:underline font-mono text-xs">
          {tid}
        </Link>
      ) : (
        <span className="font-mono text-xs">{tid}</span>
      ),
    });
  }
  if (task.created_at) {
    props.push({ label: 'Created', value: relativeAge(String(task.created_at)) });
  }
  if (task.parent_task_id) {
    props.push({ label: 'Parent', value: <IdBadge kind="task" id={String(task.parent_task_id)} /> });
  }
  // blocked-on jobs from detail envelope
  if (detailEnvelope?.blocked_on_jobs) {
    const blockedOn = detailEnvelope.blocked_on_jobs as Array<{ job_id: string; status: string }>;
    if (blockedOn.length > 0) {
      props.push({
        label: 'Blocked on',
        value: (
          <span className="inline-flex flex-wrap gap-1">
            {blockedOn.map((j) => (
              <span key={j.job_id} className="text-status-blocked font-mono text-xs">
                {j.job_id}
                <span className="text-fg-subtle ml-0.5">({j.status})</span>
              </span>
            ))}
          </span>
        ),
      });
    }
  }

  if (props.length === 0) return <></>;

  return (
    <section className="mt-6">
      <h3 className="text-fg-muted mb-2 text-xs font-medium tracking-wider uppercase">
        Properties
      </h3>
      <dl className="space-y-1 text-sm">
        {props.map(({ label, value }) => (
          <div key={label} className="flex gap-2">
            <dt className="text-fg-muted w-20 shrink-0">{label}</dt>
            <dd className="text-fg">{value}</dd>
          </div>
        ))}
      </dl>
    </section>
  );
}

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

// ── main component ──

export function TaskDetailPane({ taskId }: { taskId: string }): JSX.Element {
  const navigate = useNavigate();
  const { slug } = useParams<{ slug: string }>();
  const routes = useTasksRoutes();
  const task = useTask(taskId);
  const recall = useTaskRecall(taskId);
  const jobsQuery = useJobsList({ task_id: taskId, status: 'all', limit: 100 });
  // Full detail envelope for chain, blocked-on, and active_chain fields
  const detailQuery = useQuery({
    queryKey: ['task-detail', slug, taskId],
    queryFn: () => getTask(slug as string, taskId),
    enabled: !!slug && !!taskId,
  });
  const detail = detailQuery.data;
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

  const revisitChain: string[] = detail?.revisit_chain ?? [];
  const directRevisits: string[] = (detail?.direct_revisits as string[]) ?? [];

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
            {/* Revisit chain timeline */}
            {(revisitChain.length > 1 || directRevisits.length > 0) && (
              <RevisitChainTimeline
                revisitChain={revisitChain}
                directRevisits={directRevisits}
              />
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
                <h3 className="text-fg-muted mb-2 text-xs font-medium tracking-wider uppercase">
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

            {/* Property rail */}
            {task.data && (
              <PropertyRail
                task={task.data as Record<string, unknown>}
                detailEnvelope={detail ?? null}
              />
            )}

            {detail?.active_chain && (
              <WorkflowChainStrip chain={detail.active_chain} />
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
