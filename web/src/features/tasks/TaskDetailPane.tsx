import { useState } from 'react';
import { Link, useNavigate, useParams } from 'react-router-dom';
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
import { TaskRecallTree } from './TaskRecallTree';
import { TaskEventsLog } from './TaskEventsLog';
import { CancelTaskDialog } from './CancelTaskDialog';
import { RevisitTaskDialog } from './RevisitTaskDialog';
import { ResolveEscalationDialog } from './ResolveEscalationDialog';

const BRIEF_COLLAPSE_THRESHOLD = 600;

const TERMINAL_STATUSES: ReadonlySet<string> = new Set([
  'failed',
  'completed',
  'cancelled',
]);

export function TaskDetailPane({ taskId }: { taskId: string }): JSX.Element {
  const navigate = useNavigate();
  const { slug } = useParams<{ slug: string }>();
  const routes = useTasksRoutes();
  const task = useTask(taskId);
  const recall = useTaskRecall(taskId);
  const jobsQuery = useJobsList({ task_id: taskId, status: 'all', limit: 100 });
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
                className="bg-tier-red-tint text-status-abandoned mt-3 rounded-sm px-3 py-2 text-sm"
              >
                <span className="font-semibold">Failure reason:</span>{' '}
                <span className="font-mono">{failureNote}</span>
              </div>
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
