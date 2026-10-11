import { useState } from 'react';
import { Button } from '@/design-system/primitives/Button';
import { useTaskPauseOverview, useTasksRoutes } from '@/hooks/tasks';
import { useTranslation } from '@/hooks/i18n';
import { formatDateShapeFor } from '@/lib/i18n/format';
import type { MessageKey } from '@/lib/i18n';
import type { TaskPauseOverview as Overview } from '@/lib/api/types';
import { classifyTaskError, renderTaskError } from './strings';
import { TaskPauseOverviewRoot } from './TaskPauseControls';

const SECTIONS: { name: keyof Pick<Overview, 'unheld_runnable' | 'pausing' | 'paused' | 'terminal_drain' | 'unavailable_roots'>; key: MessageKey }[] = [
  { name: 'unheld_runnable', key: 'tasks.pause.overview.runnable' },
  { name: 'pausing', key: 'tasks.pause.pausing' },
  { name: 'paused', key: 'tasks.pause.paused' },
  { name: 'terminal_drain', key: 'tasks.pause.overview.terminal' },
  { name: 'unavailable_roots', key: 'tasks.pause.overview.unavailable' },
];

/** One page at a time: continuation responses are new observations, never summed. */
export function TaskPauseOverview(): JSX.Element {
  const { t, locale } = useTranslation();
  const routes = useTasksRoutes();
  const [before, setBefore] = useState<string | undefined>();
  const query = useTaskPauseOverview(before ? { before } : undefined);
  const data = query.data;
  const empty = data && SECTIONS.every(({ name }) => data[name].length === 0);
  return <section aria-label={t('tasks.pause.overview.title')} className="border-border-default bg-surface-raised my-4 min-w-0 space-y-3 rounded-xl border p-4">
    <div className="flex flex-wrap items-center justify-between gap-2">
      <h2 className="text-text-primary text-sm font-semibold">{t('tasks.pause.overview.title')}</h2>
      <Button size="sm" variant="ghost" disabled={query.isFetching} onClick={() => void query.refetch()}>
        {t(query.isError ? 'tasks.pause.overview.retry' : 'tasks.pause.overview.refresh')}
      </Button>
    </div>
    <p className="text-text-secondary text-xs">{t('tasks.pause.overview.explanation')}</p>
    {query.isLoading && <p role="status" className="text-text-muted text-sm">{t('tasks.pause.overview.loading')}</p>}
    {query.isError && <div role="alert" className="text-status-abandoned space-y-1 text-sm">
      <p>{t(data ? 'tasks.pause.overview.stale' : 'tasks.pause.overview.error')}</p>
      <p>{renderTaskError(classifyTaskError(query.error, 'tasks.pause.overview.error'), t)}</p>
    </div>}
    {!query.isLoading && !data && !query.isError && <p className="text-text-muted text-sm">{t('tasks.pause.unavailable')}</p>}
    {data && <>
      <p className="text-text-secondary break-words text-xs">{t('tasks.pause.overview.org', { org: data.org_slug })}</p>
      <p className="text-text-muted text-xs">{t('tasks.pause.observed', { stamp: formatDateShapeFor(locale, new Date(data.observed_at), 'dateTime') })}</p>
      <p className="text-text-secondary text-xs">{t(data.evidence_complete ? 'tasks.pause.evidenceComplete' : 'tasks.pause.evidenceIncomplete')}</p>
      {(before || data.next_cursor || !data.evidence_complete) && <p className="text-text-secondary text-xs">{t('tasks.pause.overview.partial')}</p>}
      {data.counts === null && <p className="text-text-muted text-xs">{t('tasks.pause.overview.countsUnavailable')}</p>}
      {query.isFetching && <p role="status" className="text-text-muted text-xs">{t('tasks.pause.overview.updating')}</p>}
      {empty && <p className="text-text-secondary text-sm">{t('tasks.pause.overview.empty')}</p>}
      {SECTIONS.map(({ name, key }) => data[name].length > 0 || data.counts !== null ? <div key={name} className="space-y-2">
        <h3 className="text-text-primary text-xs font-semibold">{t(key)}{data.counts?.[name] !== undefined && ` · ${data.counts[name]}`}</h3>
        <ul className="grid min-w-0 gap-2 sm:grid-cols-2">{data[name].map(pause => <TaskPauseOverviewRoot key={pause.root_task_id} pause={pause} routes={routes} />)}</ul>
      </div> : null)}
      <div className="flex flex-wrap gap-2">
        {data.next_cursor && <Button size="sm" variant="outline" disabled={query.isFetching} onClick={() => setBefore(data.next_cursor ?? undefined)}>{t('tasks.pause.overview.next')}</Button>}
        {before && <Button size="sm" variant="ghost" disabled={query.isFetching} onClick={() => setBefore(undefined)}>{t('tasks.pause.overview.first')}</Button>}
      </div>
    </>}
  </section>;
}
