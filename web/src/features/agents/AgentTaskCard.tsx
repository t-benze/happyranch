/**
 * Agent recent-task card: the pure TaskCard plus its waiting qualifier.
 *
 * Only a job-parked card with parseable blocking IDs mounts the jobs read
 * (existing jobs-by-task listing and its polling); every other card makes no
 * request. Pending, failed or incomplete reads show no finished-jobs qualifier.
 */
import { TaskCard, type TaskCardProps } from '@/design-system/patterns/TaskCard';
import { useJobsList } from '@/hooks/jobs';
import { useTranslation } from '@/hooks/i18n';
import { resolveTaskWaitingQualifier, taskWaitingLabels } from '@/shared/tasks/child-wait';
import { blockedJobsFromList, blockedOnJobIds, finishedJobsQualifier } from '@/shared/tasks/finished-jobs';

type AgentTaskCardProps = Omit<TaskCardProps, 'waitingQualifier'>;

export function AgentTaskCard(props: AgentTaskCardProps): JSX.Element {
  const { t } = useTranslation();
  const { task } = props;
  const ids = task.status === 'in_progress' && task.block_kind === 'blocked_on_job' ? blockedOnJobIds(task) : null;
  if (ids) return <JobParkedTaskCard card={props} ids={ids} />;
  return <MountedTaskCard card={props} waitingQualifier={resolveTaskWaitingQualifier(task, taskWaitingLabels(t))} />;
}

function JobParkedTaskCard({ card, ids }: { card: AgentTaskCardProps; ids: string[] }): JSX.Element {
  const { t } = useTranslation();
  const { task } = card;
  const jobs = useJobsList({ task_id: task.task_id, status: 'all', limit: 200 });
  const entries = jobs.isError ? null : blockedJobsFromList(ids, jobs.data?.jobs);
  const qualifier = finishedJobsQualifier(task, entries, t) ?? resolveTaskWaitingQualifier(task, taskWaitingLabels(t));
  return <MountedTaskCard card={card} waitingQualifier={qualifier} />;
}

/** Explicit props (no spread) so the localized-labels ownership gate still sees `labels`. */
function MountedTaskCard({ card, waitingQualifier }: { card: AgentTaskCardProps; waitingQualifier: string | null }): JSX.Element {
  return <TaskCard task={card.task} agentLabel={card.agentLabel} to={card.to} active={card.active}
    density={card.density} taskRoutes={card.taskRoutes} labels={card.labels} waitingQualifier={waitingQualifier} />;
}
