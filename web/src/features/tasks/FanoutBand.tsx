/** Current row observations and a separately attributed prior audit join. */
import type { ChildStatusCounts } from './fanout';
import { progressSummary } from './fanout';
import { childWaitState } from '@/shared/tasks/child-wait';
import type { TaskRecord } from '@/lib/api/types';
import { useTranslation } from '@/hooks/i18n';
import type { MessageKey } from '@/lib/i18n';

export type FanoutMode = 'spawned' | 'joined';
interface FanoutBandProps {
  mode: FanoutMode;
  width: number | null;
  counts: ChildStatusCounts | null;
  task: TaskRecord;
}

/** Fan-out glyph — a small branch icon echoing the design without new assets. */
function BranchIcon(): JSX.Element {
  return (
    <svg
      viewBox="0 0 24 24"
      className="h-4 w-4"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden
    >
      <circle cx="6" cy="6" r="2.2" />
      <circle cx="18" cy="6" r="2.2" />
      <circle cx="18" cy="18" r="2.2" />
      <path d="M6 8.2v3.3a3 3 0 0 0 3 3h6.8" />
      <path d="M6 8.2V6" opacity="0" />
      <path d="M15.8 6H8.2" />
    </svg>
  );
}

function ProgressBar({ counts, description }: { counts: ChildStatusCounts; description: string }): JSX.Element {
  const segments = [
    ['pending', 'bg-border-strong'], ['inProgress', 'bg-border-default'],
    ['escalated', 'bg-status-escalated'], ['completed', 'bg-status-open'],
    ['failed', 'bg-status-abandoned'], ['cancelled', 'bg-text-muted'], ['superseded', 'bg-text-secondary'],
  ] as const;
  return <div role="img" aria-label={description} className="bg-border-subtle mt-2 flex h-1.5 w-full overflow-hidden rounded-full">
    {segments.map(([category, cls]) => counts[category] > 0 && <span key={category} className={cls}
      // eslint-disable-next-line react/forbid-dom-props -- proportional literal row count, not execution progress
      style={{width: `${counts[category] / counts.total * 100}%`}} aria-hidden />)}
  </div>;
}
const HEADLINES: Record<NonNullable<ReturnType<typeof childWaitState>>, MessageKey> = {
  waiting_children: 'tasks.fanout.waitingChildren', pending_children: 'tasks.fanout.pendingChildren',
  child_results_received: 'tasks.fanout.resultsReceived', continuation_pending: 'tasks.fanout.continuationPending',
  unavailable: 'tasks.fanout.childUnavailable',
};
export function FanoutBand({mode, counts, task}: FanoutBandProps): JSX.Element {
  const { locale, t, render } = useTranslation();
  const applicable = task.status === 'in_progress' && task.block_kind === 'delegated';
  const state = childWaitState(task) ?? 'unavailable';
  const title = mode === 'joined' ? t('tasks.fanout.joined')
    : applicable ? t(HEADLINES[state]) : t('tasks.fanout.recorded');
  return <section aria-label={t('tasks.fanout.label')} className="border-border-default bg-surface-raised mt-4 rounded-xl border p-4">
    <div className="flex items-start gap-3">
      <span className="text-text-secondary mt-0.5 shrink-0"><BranchIcon /></span>
      <div className="min-w-0 flex-1">
        <p className="text-text-primary text-sm font-semibold">{title}</p>
        <p className="text-text-muted mt-1 text-xs">{t(mode === 'joined' ? 'tasks.fanout.joinedNote' : 'tasks.fanout.spawnedNote')}</p>
        {mode === 'joined' && counts && <p className="text-text-secondary mt-2 text-xs">{t('tasks.fanout.currentRows')}</p>}
        {counts ? <>
          <ProgressBar counts={counts} description={progressSummary(counts, locale)} />
          <p className="text-text-secondary mt-1.5 font-mono text-xs tabular-nums">{progressSummary(counts, locale)}</p>
        </> : <p className="text-text-secondary mt-2 text-xs">{t('tasks.fanout.countsUnavailable')}</p>}
      </div>
    </div>
    <div className="border-border-subtle text-text-muted mt-3 flex flex-wrap gap-x-4 gap-y-1 border-t pt-2 text-xs">
      {counts && <span>{render('tasks.fanout.width', {value: <span className="text-text-secondary tabular-nums">{counts.total}</span>})}</span>}
      <span>{render('tasks.fanout.join', {value: <span className="text-text-secondary font-mono">all-terminal</span>})}</span>
    </div>
  </section>;
}
