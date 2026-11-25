/**
 * Attribute history (THR-390 PR7, Screen E2). Uses the history embedded in
 * `GET /tasks/{id}/attributes` (re-read on open), never the separate history
 * endpoint. Events are append-only; nothing here edits or deletes them, and
 * the original (admission) value is shown as immutable.
 */
import '../tasks.css';
import { Button } from '@/design-system/primitives/Button';
import { Dialog, DialogContent, DialogDescription, DialogTitle } from '@/design-system/primitives/Dialog';
import { useTranslation } from '@/hooks/i18n';
import { useTaskAttributes } from '@/hooks/task-attribute-values';
import type { AttributeEvent, TaskAttributeRead, TaskAttributesRead } from '@/lib/api/types';
import { KeyChip, Note, ProvenanceBadge, ValueText, formatUtc, formatWhen, useActorLabel } from './parts';

const TH = 'text-text-muted border-border-default border-b px-2 py-1.5 text-left text-overline font-bold tracking-wider uppercase';
const TD = 'ta-htd border-border-default border-b px-2 py-2 align-top';

function RubricCell({ version, name, now }: { version: number; name: string; now?: { version: number; name: string } }): JSX.Element {
  const { t } = useTranslation();
  return (
    <>
      {t('tasks.attributes.history.rubricAt', { v: version, name })}
      {now && (now.version !== version || now.name !== name) && (
        <span className="text-text-muted block text-xs">{t('tasks.attributes.history.nowAt', { v: now.version, name: now.name })}</span>
      )}
    </>
  );
}

function HistoryBody({ read }: { read: TaskAttributesRead }): JSX.Element {
  const { t, locale } = useTranslation();
  const actorLabel = useActorLabel();
  const attrs = read.attributes;
  const admission = attrs.map((a) => a.admission).find((x) => x.state === 'recorded');
  const states = new Set(attrs.map((a) => a.history.history_state));
  const events: { e: AttributeEvent; a: TaskAttributeRead }[] = attrs
    .flatMap((a) => a.history.events.filter((e) => e.source !== 'admission').map((e) => ({ e, a })))
    .sort((x, y) => (x.e.at === y.e.at ? y.e.event_id - x.e.event_id : y.e.at.localeCompare(x.e.at)));
  if (!admission && events.length === 0) {
    return <Note tone="attention" role="status" testId="ta-history-unavailable">{t('tasks.attributes.history.unavailable')}</Note>;
  }
  const changed = attrs.filter((a) => a.initial.state === 'set' && (!a.current.present || a.current.value !== a.initial.value));
  const label = (k: Parameters<typeof t>[0]): string => t(k);
  return (
    <div className="flex min-w-0 flex-col gap-4">
      {changed.map((a) => a.initial.state === 'set' && (
        <div key={a.definition.definition_id} className="min-w-0" data-testid={`ta-ovc-${a.definition.key}`}>
          <div className="text-text-muted mb-1 flex items-center gap-2 text-xs font-bold tracking-wider uppercase">
            {a.definition.name} <span className="tracking-normal normal-case"><KeyChip>{a.definition.key}</KeyChip></span>
          </div>
          <div className="grid grid-cols-2 gap-2">
            <div className="border-border-default rounded-md border px-2.5 py-2">
              <div className="text-text-muted text-overline font-bold tracking-wider uppercase">{t('tasks.attributes.history.original')}</div>
              <ValueText type={a.definition.type} value={{ present: true, value: a.initial.value }} />
              <span className="text-text-muted block text-xs">{t('tasks.attributes.editor.immutable')}</span>
            </div>
            <div className="border-border-default rounded-md border px-2.5 py-2">
              <div className="text-text-muted text-overline font-bold tracking-wider uppercase">{t('tasks.attributes.history.current')}</div>
              <ValueText type={a.definition.type} value={a.current} />
            </div>
          </div>
        </div>
      ))}
      {admission ? (
        <section className="min-w-0">
          <h3 className="text-text-muted mb-1.5 flex flex-wrap items-center gap-2 text-xs font-bold tracking-wider uppercase">
            {t('tasks.attributes.history.before')}
            {admission.state === 'recorded' && (
              <span className="border-border-default bg-surface-sunken text-text-secondary rounded-full border px-2 text-xs font-semibold normal-case tracking-normal" data-testid="ta-admitted-by">
                {t('tasks.attributes.history.admittedBy', { actor: actorLabel(admission.actor_id, admission.actor_kind), time: formatWhen(locale, admission.at) })}
              </span>
            )}
          </h3>
          <table className="ta-htable w-full border-collapse text-xs">
            <caption className="sr-only">{t('tasks.attributes.history.before')}</caption>
            <thead><tr><th scope="col" className={TH}>{label('tasks.attributes.history.attr')}</th><th scope="col" className={TH}>{label('tasks.attributes.history.new')}</th><th scope="col" className={TH}>{label('tasks.attributes.history.rubric')}</th></tr></thead>
            <tbody>
              {attrs.map((a) => (
                <tr key={a.definition.definition_id}>
                  <td className={TD} data-l={label('tasks.attributes.history.attr')}><span className="font-mono">{a.definition.key}</span><span className="text-text-muted block text-xs">{a.definition.name}</span></td>
                  <td className={TD} data-l={label('tasks.attributes.history.new')}>
                    {a.initial.state === 'set'
                      ? <><ValueText type={a.definition.type} value={{ present: true, value: a.initial.value }} /><span className="text-text-muted block text-xs">{t('tasks.attributes.prov.supplied')}</span></>
                      : <ProvenanceBadge provenance={a.provenance} />}
                  </td>
                  <td className={TD} data-l={label('tasks.attributes.history.rubric')}>
                    {a.initial.state === 'set' ? <RubricCell version={a.initial.definition_version} name={a.initial.definition.name} /> : '—'}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </section>
      ) : null}
      <section className="min-w-0">
        <h3 className="text-text-muted mb-1.5 text-xs font-bold tracking-wider uppercase">{t('tasks.attributes.history.after')}</h3>
        {states.has('incomplete') && !admission && (
          <div className="mb-2"><Note tone="attention" role="status" testId="ta-history-incomplete">{t('tasks.attributes.history.incomplete')}</Note></div>
        )}
        {events.length === 0 ? (
          <p className="text-text-muted text-xs" data-testid="ta-history-none">{t('tasks.attributes.history.noEvents')}</p>
        ) : (
          <table className="ta-htable w-full border-collapse text-xs">
            <caption className="sr-only">{t('tasks.attributes.history.after')}</caption>
            <thead>
              <tr>
                {(['when', 'who', 'attr', 'old', 'new', 'rubric'] as const).map((k) => (
                  <th key={k} scope="col" className={TH}>{label(`tasks.attributes.history.${k}`)}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {events.map(({ e, a }) => (
                <tr key={e.event_id} data-event-id={e.event_id}>
                  <td className={TD} data-l={label('tasks.attributes.history.when')}>
                    <time dateTime={e.at} title={formatUtc(e.at)}>{formatWhen(locale, e.at)}</time>
                    <span className="text-text-muted block text-xs">{formatUtc(e.at)}</span>
                  </td>
                  <td className={TD} data-l={label('tasks.attributes.history.who')}><span className="break-words" data-testid="ta-who">{actorLabel(e.actor_id, e.actor_kind)}</span></td>
                  <td className={TD} data-l={label('tasks.attributes.history.attr')}><span className="font-mono">{e.definition.key}</span></td>
                  <td className={TD} data-l={label('tasks.attributes.history.old')}><ValueText type={e.definition.type} value={e.old} /></td>
                  <td className={TD} data-l={label('tasks.attributes.history.new')}>
                    <ValueText type={e.definition.type} value={e.new} />
                    {e.note !== undefined && e.note !== null && e.note !== '' && (
                      <span className="text-text-muted block text-xs break-words">{t('tasks.attributes.history.note')}: {e.note}</span>
                    )}
                  </td>
                  <td className={TD} data-l={label('tasks.attributes.history.rubric')}>
                    <RubricCell version={e.definition_version} name={e.definition.name} now={{ version: a.definition.version, name: a.definition.name }} />
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </section>
    </div>
  );
}

export function TaskAttributeHistoryDialog({ taskId, taskTitle, onClose }: { taskId: string; taskTitle: string; onClose: () => void }): JSX.Element {
  const { t } = useTranslation();
  const query = useTaskAttributes(taskId, true);
  const read = query.isFetching && !query.isError ? undefined : query.data;
  return (
    <Dialog open onOpenChange={(next) => { if (!next) onClose(); }}>
      <DialogContent className="flex max-w-3xl min-w-0 flex-col gap-0 overflow-hidden p-0" closeLabel={t('tasks.attributes.close')} aria-describedby="ta-history-sub">
        <div className="px-6 pt-6 pr-12 pb-3">
          <DialogTitle className="font-display text-lg">{t('tasks.attributes.history.title')}</DialogTitle>
          <DialogDescription id="ta-history-sub" className="mt-1 font-mono text-xs break-words">{taskId} · {taskTitle}</DialogDescription>
        </div>
        <div className="min-h-0 flex-1 overflow-y-auto px-6 pt-1 pb-4" data-testid="ta-history-body">
          {query.isError && !query.isFetching ? (
            <Note tone="danger" role="alert" actions={<Button size="sm" variant="outline" className="bg-surface-raised" onClick={() => void query.refetch()}>{t('tasks.attributes.retry')}</Button>}>
              {t('tasks.attributes.history.fetchError')}
            </Note>
          ) : !read ? (
            <div aria-busy="true" className="flex flex-col gap-3">
              <span className="sr-only">{t('tasks.attributes.history.loading')}</span>
              {[0, 1, 2].map((i) => <div key={i} aria-hidden="true" className="bg-surface-sunken h-4 rounded-md" />)}
            </div>
          ) : (
            <HistoryBody read={read} />
          )}
        </div>
        <div className="border-border-default flex justify-end border-t px-6 py-3">
          <Button variant="outline" className="bg-surface-raised" onClick={onClose}>{t('tasks.attributes.close')}</Button>
        </div>
      </DialogContent>
    </Dialog>
  );
}
