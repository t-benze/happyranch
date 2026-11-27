/**
 * Archive confirmation (THR-390 PR6, Screen C2).
 *
 * `archiveDefinition(slug, id, expected_version)` takes the version from this
 * dialog's own fresh per-definition read; Archive is offered only from that
 * read's `can_archive`. Refusals render `detail.current`: an already-archived
 * definition shows the read-only "archived by" state with Close only
 * (correction 5: nothing re-enables it), a newer active version asks for
 * Reload latest. A lost response is unknown until "Refresh to check"
 * proves the outcome (archived at expected+1 with all five fields unchanged).
 */
import { useEffect, useRef, useState } from 'react';
import { useParams } from 'react-router-dom';
import { Button } from '@/design-system/primitives/Button';
import { Dialog, DialogContent, DialogDescription, DialogTitle } from '@/design-system/primitives/Dialog';
import { useTranslation } from '@/hooks/i18n';
import { useIdentityPresentation } from '@/hooks/identities';
import { useDefinition, useTaskAttributeActions } from '@/hooks/task-attributes';
import type { AttributeDefinitionRead } from '@/lib/api/types';
import { useConfigOwner, useOwnerGate } from '@/shared/owners';
import { classifyWriteError, currentDefinitionRead, reconcileArchive } from './model';
import { Callout, KeyChip, formatWhen } from './parts';
import type { ListNotice } from './TaskAttributesSection';

type Status =
  | { s: 'idle' }
  | { s: 'archiving' }
  | { s: 'stale'; current: AttributeDefinitionRead }
  | { s: 'byOther'; current: AttributeDefinitionRead }
  | { s: 'permDenied' }
  | { s: 'failed' }
  | { s: 'unknown'; checking: boolean; refreshFailed: boolean }
  | { s: 'notArchived' }
  | { s: 'refused' };

export function ArchiveDefinitionDialog({
  definitionId,
  onClose,
  onArchived,
}: {
  definitionId: string;
  onClose: () => void;
  onArchived: (notice: ListNotice) => void;
}): JSX.Element {
  const { slug = '' } = useParams<{ slug: string }>();
  const { t, locale, render } = useTranslation();
  const { label } = useIdentityPresentation();
  const actions = useTaskAttributeActions();
  const read = useDefinition(definitionId);
  const openedAt = useRef(Date.now());
  const [base, setBase] = useState<AttributeDefinitionRead | null>(null);
  const [observedAt, setObservedAt] = useState<number>(0);
  const [status, setStatus] = useState<Status>({ s: 'idle' });
  const sentVersion = useRef<number | null>(null);

  const fresh = read.isSuccess && !read.isFetching && read.dataUpdatedAt >= openedAt.current ? read.data ?? null : null;
  useEffect(() => {
    if (fresh && !base) {
      setBase(fresh);
      setObservedAt(read.dataUpdatedAt);
    }
  }, [fresh, base, read.dataUpdatedAt]);

  const def = base?.definition ?? null;
  const closeOnly = status.s === 'byOther';
  const retry = status.s === 'failed' || status.s === 'notArchived';
  // Server editability only (ARCHITECTURE.md): `archived` is presentation.
  const canArchive = !!base && !!fresh && base.editability.can_archive;
  const archiveDisabled = !canArchive || status.s === 'archiving' || status.s === 'unknown' || status.s === 'stale'
    || status.s === 'permDenied' || status.s === 'refused';

  // F1: the archive POST is a draftless owner save. While it is pending or
  // its result is unknown, every dismissal holds (no second POST from a
  // reopened dialog) until same-context reconciliation settles it.
  const reconcileRef = useRef<() => Promise<'saved' | 'not_saved' | 'unknown'>>(async () => 'unknown');
  const owner = useConfigOwner({
    page: 'settings.taskAttributes',
    subject: def?.key ?? null,
    org: slug || null,
    dirty: false,
    draft: null,
    checkResult: () => reconcileRef.current(),
  });
  const { requestOwnerTransition } = useOwnerGate();
  const requestClose = (): void => {
    requestOwnerTransition({ effect: 'close', affects: (record) => record.id === owner.id, proceed: onClose });
  };

  const send = async (expected: number): Promise<void> => {
    if (!actions.isDefinitionCurrent(definitionId) && !retry) return;
    if (owner.isBusy() || owner.saveRefused) return;
    setStatus({ s: 'archiving' });
    const result = await owner.track(
      () => {
        sentVersion.current = expected;
        return actions.archive(definitionId, expected);
      },
      (error) => (classifyWriteError(error, 'archive').kind === 'unknown' ? 'unknown' : 'failed'),
    );
    if (result.ok) {
      void actions.refreshList();
      onArchived({ kind: 'archived', name: def?.name ?? '' });
      return;
    }
    if (result.refused) return setStatus(owner.saveRefused ? { s: 'unknown', checking: false, refreshFailed: false } : { s: 'idle' });
    // Desktop admission refused: nothing was sent.
    if ((result.error as Error | null)?.name === 'OwnersReceiptError') return setStatus({ s: 'failed' });
    const failure = classifyWriteError(result.error, 'archive');
    if (failure.kind === 'unknown') return setStatus({ s: 'unknown', checking: false, refreshFailed: false });
    const current = currentDefinitionRead(failure.detail);
    const code = failure.detail?.code ?? failure.code;
    if (failure.status === 403 && code === 'human_only') return setStatus({ s: 'permDenied' });
    if (failure.status === 409 && current) {
      void actions.refreshList();
      return setStatus(current.definition.archived || code === 'definition_archived' ? { s: 'byOther', current } : { s: 'stale', current });
    }
    if (failure.status >= 500) return setStatus({ s: 'failed' });
    setStatus({ s: 'refused' });
  };

  const confirm = (): void => {
    if (archiveDisabled || !def) return;
    void send(retry && sentVersion.current !== null ? sentVersion.current : def.version);
  };

  const reconcileUnknown = async (): Promise<'saved' | 'not_saved' | 'unknown'> => {
    if (!def || sentVersion.current === null) return 'unknown';
    setStatus({ s: 'unknown', checking: true, refreshFailed: false });
    try {
      const latest = await actions.readDefinition(definitionId);
      const outcome = reconcileArchive(latest.definition, def, sentVersion.current);
      if (outcome.outcome === 'committed') {
        void actions.refreshList();
        onArchived({ kind: 'archived', name: def.name });
        return 'saved';
      }
      if (outcome.outcome === 'notArchived') {
        setStatus({ s: 'notArchived' });
        return 'not_saved';
      }
      void actions.refreshList();
      setStatus(outcome.outcome === 'byOther' ? { s: 'byOther', current: latest } : { s: 'stale', current: latest });
      return 'not_saved';
    } catch {
      setStatus({ s: 'unknown', checking: false, refreshFailed: true });
      return 'unknown';
    }
  };
  reconcileRef.current = reconcileUnknown;

  const refreshToCheck = async (): Promise<void> => {
    if (status.s !== 'unknown') return;
    await owner.check().catch(() => undefined);
  };

  const reloadLatest = async (): Promise<void> => {
    try {
      const latest = await actions.readDefinition(definitionId);
      setBase(latest);
      setObservedAt(Date.now());
      sentVersion.current = null;
      setStatus(latest.definition.archived ? { s: 'byOther', current: latest } : { s: 'idle' });
    } catch {
      // The stale notice stays; nothing was sent.
    }
  };

  const lastObserved = t('settings.taskAttributes.archive.lastObserved', { time: formatWhen(locale, observedAt ? new Date(observedAt).toISOString() : null) });

  return (
    <Dialog open onOpenChange={(next) => { if (!next) requestClose(); }}>
      <DialogContent
        className="flex max-w-md min-w-0 flex-col gap-0 overflow-hidden p-0"
        closeLabel={t('settings.taskAttributes.archive.close')}
        aria-describedby="ta-archive-sub"
        onOpenAutoFocus={(e) => {
          e.preventDefault();
          (document.getElementById('ta-keep-active') as HTMLButtonElement | null)?.focus();
        }}
      >
        <div className="px-6 pt-6 pb-3 pr-12">
          <DialogTitle className="font-display text-lg break-words">
            {def ? t('settings.taskAttributes.archive.title', { name: def.name }) : t('settings.taskAttributes.detail.loading')}
          </DialogTitle>
          <DialogDescription id="ta-archive-sub" className="mt-1 font-mono text-xs break-all">{def?.key ?? ''}</DialogDescription>
        </div>
        <div className="min-h-0 flex-1 overflow-y-auto px-6 pb-4">
          {!def ? (
            read.isError && !read.isFetching ? (
              <Callout tone="danger" role="alert" actions={<Button variant="outline" className="bg-surface-raised" size="sm" onClick={() => void read.refetch()}>{t('settings.taskAttributes.list.retry')}</Button>}>
                {t('settings.taskAttributes.editor.loadError')}
              </Callout>
            ) : (
              <p role="status" aria-busy="true" className="text-text-secondary text-sm">{t('settings.taskAttributes.detail.loading')}</p>
            )
          ) : (
            <div className="flex min-w-0 flex-col gap-3 text-sm">
              {status.s === 'archiving' && <p role="status" className="sr-only">{t('settings.taskAttributes.editor.saving')}</p>}
              {status.s === 'unknown' && (
                <Callout
                  tone="attention"
                  role="alert"
                  title={t('settings.taskAttributes.editor.unknownTitle')}
                  actions={<Button variant="outline" className="bg-surface-raised" size="sm" loading={status.checking} disabled={status.checking} onClick={() => void refreshToCheck()}>{t('settings.taskAttributes.editor.refresh')}</Button>}
                >
                  {t('settings.taskAttributes.editor.unknownBody')}
                  {status.refreshFailed && <> {t('settings.taskAttributes.editor.refreshFailed')}</>}
                </Callout>
              )}
              {status.s === 'notArchived' && <Callout tone="attention" role="alert">{t('settings.taskAttributes.archive.notArchived')}</Callout>}
              {status.s === 'stale' && (
                <Callout tone="attention" role="alert">{t('settings.taskAttributes.archive.stale', { v: status.current.definition.version })}</Callout>
              )}
              {status.s === 'byOther' && (
                <Callout tone="info" role="alert">
                  {t('settings.taskAttributes.archive.byOther', { actor: label('founder'), time: formatWhen(locale, status.current.definition.archived_at) })}
                </Callout>
              )}
              {status.s === 'permDenied' && (
                <Callout tone="danger" role="alert">{t('settings.taskAttributes.archive.permDenied', { org: slug })} {lastObserved}</Callout>
              )}
              {status.s === 'failed' && <Callout tone="danger" role="alert">{t('settings.taskAttributes.archive.failed')} {lastObserved}</Callout>}
              {status.s === 'refused' && <Callout tone="danger" role="alert">{t('settings.taskAttributes.editor.refused')}</Callout>}
              {status.s !== 'byOther' && (
                <p className="break-words">
                  {render('settings.taskAttributes.archive.body1', { key: <KeyChip value={def.key} /> })}
                </p>
              )}
              <p>{t('settings.taskAttributes.archive.body2')}</p>
              {status.s !== 'byOther' && <p>{t('settings.taskAttributes.archive.body3')}</p>}
            </div>
          )}
        </div>
        <div className="border-border-default flex flex-wrap items-center justify-end gap-2 border-t px-6 py-3">
          {closeOnly ? (
            <Button id="ta-keep-active" variant="outline" className="bg-surface-raised" onClick={requestClose}>{t('settings.taskAttributes.archive.close')}</Button>
          ) : (
            <>
              <Button id="ta-keep-active" variant="outline" className="bg-surface-raised" onClick={requestClose}>{t('settings.taskAttributes.archive.keepActive')}</Button>
              {status.s === 'stale' && (
                <Button variant="outline" className="bg-surface-raised" onClick={() => void reloadLatest()}>{t('settings.taskAttributes.editor.reload')}</Button>
              )}
              <Button variant="destructive" onClick={confirm} disabled={archiveDisabled} loading={status.s === 'archiving'}>
                {retry ? t('settings.taskAttributes.editor.retry') : t('settings.taskAttributes.archive.confirm')}
              </Button>
            </>
          )}
        </div>
      </DialogContent>
    </Dialog>
  );
}
