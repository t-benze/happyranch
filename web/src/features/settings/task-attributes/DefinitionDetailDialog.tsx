/**
 * Definition detail + version history (THR-390 PR6, Screen C).
 *
 * - The current version comes from this dialog's own `getDefinition(id)`;
 *   its footer Edit/Archive come only from that read's `can_*`.
 * - History (R2): older versions are read lazily with
 *   `getDefinition(id, {version})`, in all-or-nothing pages (5 rows
 *   including the current one, then up to 5 per "Load earlier"). A failed
 *   page shows "unavailable" with Retry; "No earlier versions" shows only at
 *   version 1. No new route.
 * - Actor (R1): PR2 definition writes are human-only and audited as the
 *   canonical founder identity, so every version's actor is rendered with
 *   `useIdentityPresentation().label('founder')` — never a literal name.
 */
import { useCallback, useEffect, useState } from 'react';
import { useParams } from 'react-router-dom';
import { Button } from '@/design-system/primitives/Button';
import { Dialog, DialogContent, DialogDescription, DialogTitle } from '@/design-system/primitives/Dialog';
import { Badge } from '@/design-system/primitives/Badge';
import { TONE_CLASS } from '@/design-system/patterns/semanticTone';
import { useTranslation } from '@/hooks/i18n';
import { useIdentityPresentation } from '@/hooks/identities';
import { useDefinition, useDefinitionVersions } from '@/hooks/task-attributes';
import type { AttributeDefinition } from '@/lib/api/types';
import { anyEditAllowed } from './model';
import { Callout, KeyChip, Overline, StateBadge, constraintSummary, formatWhen, refusalKey, typeLabel } from './parts';

const FIRST_PAGE = 4;
const PAGE = 5;

function pageBelow(version: number, size: number): number[] {
  const versions: number[] = [];
  for (let v = version - 1; v >= 1 && versions.length < size; v -= 1) versions.push(v);
  return versions;
}

export function DefinitionDetailDialog({
  definitionId,
  offerEdit,
  offerArchive,
  onClose,
  onEdit,
  onArchive,
}: {
  definitionId: string;
  offerEdit: boolean;
  offerArchive: boolean;
  onClose: () => void;
  onEdit: () => void;
  onArchive: () => void;
}): JSX.Element {
  const { slug = '' } = useParams<{ slug: string }>();
  const { t, locale } = useTranslation();
  const { label } = useIdentityPresentation();
  const read = useDefinition(definitionId);
  const current = read.data?.definition ?? null;
  const editability = read.data?.editability ?? null;
  const settled = read.isSuccess && !read.isFetching;
  const actor = label('founder');

  const [pages, setPages] = useState<number[][]>([]);
  const [loaded, setLoaded] = useState<Map<number, AttributeDefinition>>(new Map());
  const currentVersion = current?.version ?? 0;
  useEffect(() => {
    if (currentVersion > 1) setPages([pageBelow(currentVersion, FIRST_PAGE)]);
    else setPages([]);
    setLoaded(new Map());
  }, [currentVersion]);
  const onLoaded = useCallback((defs: AttributeDefinition[]) => {
    setLoaded((prev) => {
      const next = new Map(prev);
      for (const d of defs) next.set(d.version, d);
      return next;
    });
  }, []);

  const rows = current ? [current, ...[...loaded.values()].sort((a, b) => b.version - a.version)] : [];
  const byVersion = new Map(rows.map((d) => [d.version, d]));
  const lastRequested = pages.length ? Math.min(...(pages[pages.length - 1] ?? [currentVersion])) : currentVersion;
  const allPagesLoaded = pages.every((p) => p.every((v) => loaded.has(v)));
  const remaining = lastRequested - 1;
  const readonlyCode = editability?.refusal_reason?.code;

  return (
    <Dialog open onOpenChange={(next) => { if (!next) onClose(); }}>
      <DialogContent className="flex max-w-xl min-w-0 flex-col gap-0 overflow-hidden p-0" closeLabel={t('settings.taskAttributes.detail.close')} aria-describedby="ta-detail-sub">
        <div className="px-6 pt-6 pb-3 pr-12">
          <DialogTitle className="font-display text-lg break-words">{current?.name ?? t('settings.taskAttributes.detail.loading')}</DialogTitle>
          <DialogDescription id="ta-detail-sub" className="mt-1 font-mono text-xs break-all">{current?.key ?? ''}</DialogDescription>
        </div>
        <div className="min-h-0 flex-1 overflow-y-auto px-6 pb-4">
          {!current ? (
            read.isError ? (
              <Callout tone="danger" role="alert" actions={<Button variant="outline" size="sm" onClick={() => void read.refetch()}>{t('settings.taskAttributes.list.retry')}</Button>}>
                {t('settings.taskAttributes.editor.loadError')}
              </Callout>
            ) : (
              <p role="status" aria-busy="true" className="text-text-secondary text-sm">{t('settings.taskAttributes.detail.loading')}</p>
            )
          ) : (
            <div className="flex min-w-0 flex-col gap-4">
              {readonlyCode && readonlyCode !== 'definition_archived' && (
                <Callout tone="info">
                  {t('settings.taskAttributes.detail.detailNote', { org: slug })} {t(refusalKey(readonlyCode))}
                </Callout>
              )}
              <dl className="grid min-w-0 grid-cols-1 gap-x-4 gap-y-1 text-sm sm:grid-cols-4 sm:gap-y-2">
                <dt className="text-text-secondary sm:col-span-1">{t('settings.taskAttributes.detail.key')}</dt>
                <dd className="min-w-0 sm:col-span-3"><KeyChip value={current.key} /></dd>
                <dt className="text-text-secondary sm:col-span-1">{t('settings.taskAttributes.detail.type')}</dt>
                <dd className="sm:col-span-3">{typeLabel(t, current.type)}</dd>
                <dt className="text-text-secondary sm:col-span-1">{t('settings.taskAttributes.detail.validation')}</dt>
                <dd className="break-words sm:col-span-3">{constraintSummary(t, current)}</dd>
                <dt className="text-text-secondary sm:col-span-1">{t('settings.taskAttributes.detail.state')}</dt>
                <dd className="flex min-w-0 flex-wrap items-center gap-2 sm:col-span-3">
                  <StateBadge archived={current.archived} />
                  {!current.archived && editability && !editability.can_edit_structure && editability.can_edit_metadata && (
                    <span className="text-text-secondary text-xs">{t('settings.taskAttributes.detail.locked')}</span>
                  )}
                </dd>
                <dt className="text-text-secondary sm:col-span-1">{t('settings.taskAttributes.detail.version')}</dt>
                <dd className="break-words sm:col-span-3">
                  {t('settings.taskAttributes.detail.lastEdit', { v: current.version, actor, time: formatWhen(locale, current.recorded_at) })}
                </dd>
              </dl>
              {current.archived && (
                <Callout tone="info">
                  {t('settings.taskAttributes.detail.archivedNote')}{' '}
                  {t('settings.taskAttributes.detail.archivedOn', { actor, time: formatWhen(locale, current.archived_at) })}
                </Callout>
              )}
              {current.description && (
                <section className="flex min-w-0 flex-col gap-2">
                  <Overline>{t('settings.taskAttributes.detail.rubric')}</Overline>
                  <p className="border-border-default bg-surface-raised rounded-md border p-3 text-sm break-words whitespace-pre-wrap">{current.description}</p>
                </section>
              )}
              <section className="flex min-w-0 flex-col gap-2" aria-labelledby="ta-history-heading">
                <h3 id="ta-history-heading"><Overline>{t('settings.taskAttributes.detail.history')}</Overline></h3>
                {current.version > 1 && (
                  <p className="text-text-secondary text-xs">{t('settings.taskAttributes.detail.versionsShown', { a: rows.length, b: current.version })}</p>
                )}
                <ol reversed start={current.version} className="border-border-default bg-surface-raised list-none rounded-md border">
                  {rows.map((d) => (
                    <li key={d.version} className="border-border-default flex min-w-0 flex-col gap-0.5 border-b px-3 py-2 last:border-b-0">
                      <div className="flex min-w-0 flex-wrap items-center gap-2 text-sm">
                        <span className="text-text-primary font-semibold break-words">{t('settings.taskAttributes.detail.versionRow', { v: d.version, name: d.name })}</span>
                        {d.version === current.version && <Badge toneClass={TONE_CLASS.positive}>{t('settings.taskAttributes.detail.current')}</Badge>}
                      </div>
                      <p className="text-text-secondary text-xs break-words">
                        {t('settings.taskAttributes.detail.versionMeta', { actor, time: formatWhen(locale, d.recorded_at) })}
                        {changeSummary(t, d, byVersion.get(d.version - 1)) && <> · {changeSummary(t, d, byVersion.get(d.version - 1))}</>}
                      </p>
                    </li>
                  ))}
                </ol>
                {pages.map((versions) => (
                  <HistoryPage key={versions.join(',')} definitionId={definitionId} versions={versions} onLoaded={onLoaded} />
                ))}
                {current.version === 1 && <p className="text-text-secondary text-sm">{t('settings.taskAttributes.detail.noEarlier')}</p>}
                {allPagesLoaded && remaining > 0 && (
                  <Button variant="outline" size="sm" className="self-start" onClick={() => setPages((p) => [...p, pageBelow(lastRequested, PAGE)])}>
                    {t('settings.taskAttributes.detail.loadEarlier', { n: Math.min(PAGE, remaining) })}
                  </Button>
                )}
              </section>
            </div>
          )}
        </div>
        <div className="border-border-default flex flex-wrap items-center justify-end gap-2 border-t px-6 py-3">
          <Button variant="ghost" onClick={onClose}>{t('settings.taskAttributes.detail.close')}</Button>
          {settled && editability && offerArchive && editability.can_archive && (
            <Button variant="ghost" onClick={onArchive}>{t('settings.taskAttributes.detail.archive')}</Button>
          )}
          {settled && editability && offerEdit && anyEditAllowed(editability) && (
            <Button variant="outline" className="bg-surface-raised" onClick={onEdit}>{t('settings.taskAttributes.detail.edit')}</Button>
          )}
        </div>
      </DialogContent>
    </Dialog>
  );
}

function HistoryPage({
  definitionId,
  versions,
  onLoaded,
}: {
  definitionId: string;
  versions: number[];
  onLoaded: (defs: AttributeDefinition[]) => void;
}): JSX.Element | null {
  const { t } = useTranslation();
  const page = useDefinitionVersions(definitionId, versions, true);
  useEffect(() => {
    if (page.isSuccess && page.data) onLoaded(page.data.map((r) => r.definition));
  }, [page.isSuccess, page.data, onLoaded]);
  if (page.isSuccess) return null;
  if (page.isError && !page.isFetching) {
    return (
      <Callout
        tone="attention"
        role="alert"
        actions={<Button variant="outline" size="sm" onClick={() => void page.refetch()}>{t('settings.taskAttributes.list.retry')}</Button>}
      >
        {t('settings.taskAttributes.detail.historyUnavailable')}
      </Callout>
    );
  }
  return (
    <div aria-busy="true" className="flex flex-col gap-1">
      <span className="sr-only" role="status">{t('settings.taskAttributes.detail.historyLoadingSr')}</span>
      {versions.map((v) => <div key={v} aria-hidden="true" className="bg-surface-raised h-10 rounded-md" />)}
    </div>
  );
}

function changeSummary(t: ReturnType<typeof useTranslation>['t'], d: AttributeDefinition, prev: AttributeDefinition | undefined): string {
  if (d.version === 1) return t('settings.taskAttributes.detail.vCreated');
  if (!prev) return '';
  const parts: string[] = [];
  if (prev.name !== d.name) parts.push(t('settings.taskAttributes.detail.vRenamed', { a: prev.name, b: d.name }));
  if (prev.description !== d.description) parts.push(t('settings.taskAttributes.detail.vReworded'));
  if (prev.key !== d.key) parts.push(t('settings.taskAttributes.detail.vKey', { a: prev.key, b: d.key }));
  if (prev.type !== d.type || JSON.stringify(prev.constraints) !== JSON.stringify(d.constraints)) parts.push(t('settings.taskAttributes.detail.vStructure'));
  if (!prev.archived && d.archived) parts.push(t('settings.taskAttributes.detail.vArchived'));
  return parts.join(' · ');
}
