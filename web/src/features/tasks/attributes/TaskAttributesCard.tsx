/**
 * Task detail › Attributes card (THR-390 PR7, Screen D), mounted in the
 * TaskStatusCard rail behind the build-time flag `task_attributes_ui`.
 *
 * - Lists EVERY item the server returns in `attributes[]` (all active
 *   definitions, plus archived ones with evidence; EM ruling Q1). Unset rows
 *   read "Not set" with their server provenance.
 * - Editability renders only the server `editability`; it receives the task
 *   ID, never the task status (correction 3).
 * - Its own fetch error is isolated: the rest of the task page is unaffected.
 */
import { useRef, useState } from 'react';
import { Button } from '@/design-system/primitives/Button';
import { useTranslation } from '@/hooks/i18n';
import { useTaskAttributes } from '@/hooks/task-attribute-values';
import type { TaskAttributeRead, TaskAttributesRead } from '@/lib/api/types';
import { isArchivedField, latestEvent } from './model';
import {
  ArchivedBadge, KeyChip, Note, ProvenanceBadge, RefusalNotice, ValueText, constraintSummary, formatWhen, useActorLabel,
} from './parts';
import { TaskAttributesEditorDialog } from './TaskAttributesEditorDialog';
import { TaskAttributeHistoryDialog } from './TaskAttributeHistoryDialog';

function AttributeRow({ a }: { a: TaskAttributeRead }): JSX.Element {
  const { t, locale } = useTranslation();
  const actorLabel = useActorLabel();
  const d = a.definition;
  const ev = latestEvent(a);
  const initial = a.initial.state === 'set' ? a.initial : null;
  const showOriginal = initial !== null && (!a.current.present || a.current.value !== initial.value);
  const admission = a.admission.state === 'recorded' ? a.admission : null;
  const who = ev
    ? t('tasks.attributes.card.setBy', { actor: actorLabel(ev.actor_id, ev.actor_kind), time: formatWhen(locale, ev.at) })
    : '';
  return (
    <li className="border-border-default min-w-0 border-b py-3 last:border-b-0" data-testid={`ta-row-${d.key}`} data-admission={admission ? 'recorded' : 'none'}>
      <div className="flex min-w-0 flex-wrap items-baseline gap-2">
        <span className="text-text-secondary text-xs font-semibold break-words">{d.name}</span>
        <KeyChip>{d.key}</KeyChip>
        {isArchivedField(a) && <ArchivedBadge />}
        <span className="ml-auto min-w-0 text-right"><ValueText type={d.type} value={a.current} /></span>
      </div>
      <div className="text-text-muted mt-1.5 flex min-w-0 flex-wrap items-center gap-1.5 text-xs">
        <ProvenanceBadge provenance={a.provenance} />
        {showOriginal && initial && (
          <span className="text-text-secondary font-mono" data-testid="ta-original">
            {t('tasks.attributes.card.original', { value: initial.value === '' ? '""' : String(typeof initial.value === 'boolean' ? (initial.value ? t('tasks.attributes.value.true') : t('tasks.attributes.value.false')) : initial.value) })}
          </span>
        )}
        {who && <span className="break-words">{who}</span>}
      </div>
      <details className="mt-1.5">
        <summary className="text-info inline-flex min-h-6 cursor-pointer items-center text-xs font-semibold">
          {t('tasks.attributes.card.rubric')} · {constraintSummary(t, d)}
        </summary>
        <p className="text-text-secondary mt-1 text-xs break-words whitespace-pre-wrap">{d.description || t('tasks.attributes.card.noRubric')}</p>
      </details>
    </li>
  );
}

export function TaskAttributesCard({ taskId, taskTitle }: { taskId: string; taskTitle: string }): JSX.Element {
  const { t, locale } = useTranslation();
  const query = useTaskAttributes(taskId);
  const actorLabel = useActorLabel();
  const [dialog, setDialog] = useState<null | 'edit' | 'history'>(null);
  const [saved, setSaved] = useState<{ actor: string; at: string } | null>(null);
  const editRef = useRef<HTMLButtonElement | null>(null);
  const historyRef = useRef<HTMLButtonElement | null>(null);
  const read = query.data;
  const loading = !read && !query.isError;
  const failed = !read && query.isError;
  const rows = read?.attributes ?? [];

  const close = (): void => {
    const origin = dialog === 'edit' ? editRef.current : historyRef.current;
    setDialog(null);
    window.setTimeout(() => origin?.focus(), 0);
  };

  const onSaved = (next: TaskAttributesRead): void => {
    let latest: { actor: string; at: string } | null = null;
    for (const a of next.attributes) {
      const ev = latestEvent(a);
      if (ev && (!latest || ev.at > latest.at)) latest = { actor: actorLabel(ev.actor_id, ev.actor_kind), at: ev.at };
    }
    setSaved(latest);
  };

  return (
    <section
      aria-labelledby="ta-card-title"
      data-testid="task-attributes-card"
      className="border-border-default bg-surface-raised mt-4 min-w-0 rounded-md border"
    >
      <div className="border-border-default flex min-w-0 flex-wrap items-center gap-2 border-b px-4 py-3">
        <h2 id="ta-card-title" className="text-text-secondary text-xs font-semibold tracking-wider uppercase">{t('tasks.attributes.card.title')}</h2>
        <span className="flex-1" />
        {read && rows.length > 0 && (
          <>
            <Button ref={historyRef} size="sm" variant="ghost" onClick={() => setDialog('history')}>{t('tasks.attributes.card.history')}</Button>
            {read.editability.can_edit && (
              <Button ref={editRef} size="sm" variant="outline" className="bg-surface-raised" onClick={() => { setSaved(null); setDialog('edit'); }}>
                {t('tasks.attributes.card.edit')}
              </Button>
            )}
          </>
        )}
      </div>
      <div className="min-w-0 px-4">
        {loading && (
          <div aria-busy="true" className="flex flex-col gap-2 py-3" data-testid="ta-card-loading">
            <span className="sr-only">{t('tasks.attributes.card.loading')}</span>
            <div aria-hidden="true" className="bg-surface-sunken h-3 w-3/5 rounded" />
            <div aria-hidden="true" className="bg-surface-sunken h-3 w-2/5 rounded" />
            <div aria-hidden="true" className="bg-surface-sunken h-3 w-4/5 rounded" />
          </div>
        )}
        {failed && (
          <div className="py-3">
            <Note
              tone="danger"
              role="alert"
              actions={<Button size="sm" variant="outline" className="bg-surface-raised" onClick={() => void query.refetch()}>{t('tasks.attributes.retry')}</Button>}
            >
              {t('tasks.attributes.card.fetchError')}
            </Note>
          </div>
        )}
        {read && rows.length === 0 && (
          <p className="text-text-muted py-3 text-sm">{t('tasks.attributes.card.noDefs')}</p>
        )}
        {read && rows.length > 0 && (
          <>
            {(!read.editability.can_edit || saved) && (
              <div className="flex flex-col gap-2 pt-3">
                <RefusalNotice read={read} />
                {saved && read.editability.can_edit && (
                  <div aria-live="polite">
                    <Note tone="positive" role="status" testId="ta-saved">
                      <strong className="font-semibold">{t('tasks.attributes.card.saved')}</strong>
                      {` · ${saved.actor} · ${formatWhen(locale, saved.at)}`}
                    </Note>
                  </div>
                )}
              </div>
            )}
            <ul className="min-w-0">
              {rows.map((a) => <AttributeRow key={a.definition.definition_id} a={a} />)}
            </ul>
          </>
        )}
      </div>
      {read && rows.length > 0 && (
        <p className="border-border-default text-text-muted border-t px-4 py-2.5 text-xs">{t('tasks.attributes.card.scopeNote')}</p>
      )}
      {dialog === 'edit' && read && (
        <TaskAttributesEditorDialog taskId={taskId} taskTitle={taskTitle} onClose={close} onSaved={onSaved} />
      )}
      {dialog === 'history' && read && (
        <TaskAttributeHistoryDialog taskId={taskId} taskTitle={taskTitle} onClose={close} />
      )}
    </section>
  );
}
