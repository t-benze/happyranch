/**
 * Settings › Task attributes — definitions list (THR-390 PR6, Screen A).
 *
 * Authority (R4): the list's top-level `editability` decides whether row
 * actions are offered at all. When they are, every row of the shown tab gets
 * its own `GET …/definitions/{id}` (at most 4 in flight), and that read alone
 * enables or disables the row's Edit/Archive. `archived` / `first_used_at`
 * drive only badges, tab membership and counts. While the list read is stale
 * (a refetch failed) every write action is paused (MEM-300).
 *
 * The list has its own definitions query (not the `useSettings` gate); the
 * route wraps it in `NativeRecoveryFence`.
 */
import { Plus, Search, Tag } from 'lucide-react';
import { useMemo, useRef, useState } from 'react';
import { useParams } from 'react-router-dom';
import { Button } from '@/design-system/primitives/Button';
import { Input } from '@/design-system/primitives/Input';
import { Tabs, TabsList, TabsTrigger } from '@/design-system/primitives/Tabs';
import { useTranslation } from '@/hooks/i18n';
import { useDefinitionList, useDefinitionReads } from '@/hooks/task-attributes';
import type { AttributeDefinition, AttributeDefinitionEditability, AttributeDefinitionRead } from '@/lib/api/types';
import type { TaskAttributesQuery } from '@/design-system/providers/DataContext';
import { anyEditAllowed } from './model';
import {
  Callout,
  DupNameBadge,
  KeyChip,
  Overline,
  StateBadge,
  UnusedBadge,
  constraintSummary,
  formatWhen,
  refusalKey,
  typeLabel,
} from './parts';
import { DefinitionEditorDialog } from './DefinitionEditorDialog';
import { DefinitionDetailDialog } from './DefinitionDetailDialog';
import { ArchiveDefinitionDialog } from './ArchiveDefinitionDialog';

type TabKey = 'active' | 'archived';
export type DialogState =
  | { kind: 'create' }
  | { kind: 'edit'; id: string }
  | { kind: 'detail'; id: string }
  | { kind: 'archive'; id: string }
  | null;

export interface ListNotice {
  kind: 'created' | 'updated' | 'archived';
  v?: number;
  name?: string;
}

export function TaskAttributesSection(): JSX.Element {
  const { slug = '' } = useParams<{ slug: string }>();
  const { t, locale } = useTranslation();
  const list = useDefinitionList();
  const [tab, setTab] = useState<TabKey>('active');
  const [q, setQ] = useState('');
  const [dialog, setDialog] = useState<DialogState>(null);
  const [notice, setNotice] = useState<ListNotice | null>(null);
  const opener = useRef<HTMLElement | null>(null);
  const createRef = useRef<HTMLButtonElement>(null);

  const data = list.data;
  const top = data?.editability;
  const stale = !!data && list.isError;
  const offerEdit = !!top && anyEditAllowed(top);
  const offerArchive = !!top && top.can_archive;
  const offerRowActions = offerEdit || offerArchive;

  const definitions = useMemo(() => data?.definitions ?? [], [data]);
  const counts = useMemo(() => ({
    active: definitions.filter((d) => !d.archived).length,
    archived: definitions.filter((d) => d.archived).length,
  }), [definitions]);
  const tabRows = useMemo(() => definitions.filter((d) => (tab === 'archived' ? d.archived : !d.archived)), [definitions, tab]);
  const tabIds = useMemo(() => tabRows.map((d) => d.definition_id), [tabRows]);
  const needle = q.trim().toLowerCase();
  const shown = needle
    ? tabRows.filter((d) => d.name.toLowerCase().includes(needle) || d.key.toLowerCase().includes(needle))
    : tabRows;
  const rowReads = useDefinitionReads(tabIds, offerRowActions && list.isSuccess);
  const duplicates = useMemo(() => duplicateNames(definitions), [definitions]);

  const open = (next: DialogState, from: HTMLElement | null): void => {
    opener.current = from;
    setNotice(null);
    setDialog(next);
  };
  const closeDialog = (): void => {
    setDialog(null);
    const target = opener.current;
    // Return focus to the control that opened the dialog when it still exists.
    requestAnimationFrame(() => (target?.isConnected ? target : createRef.current)?.focus());
  };

  const refusalNote = top?.refusal_reason?.code;
  const header = (
    <div className="mb-4 flex min-w-0 flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
      <div className="min-w-0">
        <h2 className="font-display mb-1 text-lg font-semibold">{t('settings.taskAttributes.list.title')}</h2>
        <p className="text-text-secondary text-sm">{t('settings.taskAttributes.list.intro')}</p>
        <p className="text-text-secondary mt-1 text-xs">{t('settings.taskAttributes.list.org', { org: slug })}</p>
      </div>
      {data && top?.can_create && (
        <Button
          ref={createRef}
          className="shrink-0 self-start"
          disabled={stale}
          onClick={(e) => open({ kind: 'create' }, e.currentTarget)}
        >
          <Plus aria-hidden="true" />
          {t('settings.taskAttributes.list.create')}
        </Button>
      )}
    </div>
  );

  if (list.isLoading || (!data && !list.isError)) {
    return (
      <div className="max-w-3xl min-w-0 p-4 sm:p-8">
        {header}
        <Toolbar tab={tab} setTab={setTab} q={q} setQ={setQ} disabled counts={null} />
        <div aria-busy="true" className="border-border-default bg-surface-raised mt-3 rounded-lg border">
          <span className="sr-only" role="status">{t('settings.taskAttributes.list.loadingSr')}</span>
          {[0, 1, 2].map((i) => (
            <div key={i} className="border-border-default flex flex-col gap-2 border-b p-4 last:border-b-0" aria-hidden="true">
              <div className="bg-surface-sunken h-3 w-20 rounded-sm" />
              <div className="bg-surface-sunken h-4 w-48 rounded-sm" />
              <div className="bg-surface-sunken h-3 w-full max-w-md rounded-sm" />
            </div>
          ))}
        </div>
      </div>
    );
  }

  if (!data) {
    return (
      <div className="max-w-3xl min-w-0 p-4 sm:p-8">
        {header}
        <Callout
          tone="danger"
          role="alert"
          title={t('settings.taskAttributes.list.loadError')}
          actions={<Button variant="outline" size="sm" onClick={() => void list.refetch()}>{t('settings.taskAttributes.list.retry')}</Button>}
        >
          {t('settings.taskAttributes.list.loadErrorBody')}
        </Callout>
      </div>
    );
  }

  return (
    <div className="max-w-3xl min-w-0 p-4 sm:p-8">
      {header}
      <div role="status" aria-live="polite" className="empty:hidden mb-3">
        {notice && (
          <Callout tone="info">
            {notice.kind === 'created' && t('settings.taskAttributes.list.created', { v: notice.v ?? '' })}
            {notice.kind === 'updated' && t('settings.taskAttributes.list.updated', { v: notice.v ?? '' })}
            {notice.kind === 'archived' && t('settings.taskAttributes.list.archivedDone', { name: notice.name ?? '' })}
          </Callout>
        )}
      </div>
      {stale && (
        <div className="mb-3">
          <Callout
            tone="attention"
            title={t('settings.taskAttributes.list.stale')}
            actions={<Button variant="outline" size="sm" onClick={() => void list.refetch()}>{t('settings.taskAttributes.list.retry')}</Button>}
          >
            {t('settings.taskAttributes.list.staleBody', { org: slug, time: formatWhen(locale, new Date(list.dataUpdatedAt).toISOString()) })}
          </Callout>
        </div>
      )}
      {refusalNote && (
        <div className="mb-3">
          {refusalNote === 'human_only' ? (
            <Callout tone="info" title={t('settings.taskAttributes.list.readonly')}>{t('settings.taskAttributes.list.readonlyBody')}</Callout>
          ) : refusalNote === 'authority_unknown' ? (
            <Callout tone="attention" title={t('settings.taskAttributes.list.authUnknown')}>{t('settings.taskAttributes.list.authUnknownBody')}</Callout>
          ) : (
            <Callout tone="attention">{t('settings.taskAttributes.refusal.generic')}</Callout>
          )}
        </div>
      )}
      {definitions.length === 0 ? (
        <div className="border-border-default bg-surface-raised flex flex-col items-center gap-2 rounded-lg border px-4 py-10 text-center">
          <Tag size={24} aria-hidden="true" className="text-text-muted" />
          <h3 className="text-text-primary font-semibold">{t('settings.taskAttributes.list.emptyTitle')}</h3>
          <p className="text-text-secondary max-w-md text-sm">{t('settings.taskAttributes.list.emptyBody')}</p>
        </div>
      ) : (
        <>
          <Toolbar tab={tab} setTab={(next) => { setTab(next); setNotice(null); }} q={q} setQ={setQ} disabled={false} counts={counts} />
          {shown.length === 0 ? (
            <div className="border-border-default bg-surface-raised mt-3 flex flex-col items-center gap-2 rounded-lg border px-4 py-8 text-center">
              {needle ? (
                <>
                  <h3 className="text-text-primary font-semibold break-words">{t('settings.taskAttributes.list.noMatch', { q })}</h3>
                  <p className="text-text-secondary text-sm">{t('settings.taskAttributes.list.noMatchBody')}</p>
                  <Button variant="outline" size="sm" onClick={() => setQ('')}>{t('settings.taskAttributes.list.clearSearch')}</Button>
                </>
              ) : (
                <>
                  {/* Empty copy follows the selected tab (F3). */}
                  <h3 className="text-text-primary font-semibold">
                    {t(tab === 'active' ? 'settings.taskAttributes.list.activeEmpty' : 'settings.taskAttributes.list.archivedEmpty')}
                  </h3>
                  <p className="text-text-secondary text-sm">
                    {t(tab === 'active' ? 'settings.taskAttributes.list.activeEmptyBody' : 'settings.taskAttributes.detail.archivedNote')}
                  </p>
                </>
              )}
            </div>
          ) : (
            <ul className="border-border-default bg-surface-raised mt-3 min-w-0 rounded-lg border">
              {shown.map((d) => (
                <DefinitionRow
                  key={d.definition_id}
                  definition={d}
                  duplicateOf={duplicates.get(d.definition_id) ?? null}
                  offerEdit={offerEdit}
                  offerArchive={offerArchive}
                  read={rowReads[d.definition_id]}
                  paused={stale}
                  onOpen={(el) => open({ kind: 'detail', id: d.definition_id }, el)}
                  onEdit={(el) => open({ kind: 'edit', id: d.definition_id }, el)}
                  onArchive={(el) => open({ kind: 'archive', id: d.definition_id }, el)}
                />
              ))}
            </ul>
          )}
        </>
      )}
      <Dialogs
        dialog={dialog}
        setDialog={setDialog}
        onClose={closeDialog}
        onNotice={setNotice}
        listEditability={top ?? null}
      />
    </div>
  );
}

function Dialogs({
  dialog,
  setDialog,
  onClose,
  onNotice,
  listEditability,
}: {
  dialog: DialogState;
  setDialog: (next: DialogState) => void;
  onClose: () => void;
  onNotice: (notice: ListNotice) => void;
  listEditability: AttributeDefinitionEditability | null;
}): JSX.Element | null {
  if (!dialog) return null;
  if (dialog.kind === 'create') {
    return <DefinitionEditorDialog mode="create" onClose={onClose} onSaved={(n) => { onNotice(n); onClose(); }} />;
  }
  if (dialog.kind === 'edit') {
    return <DefinitionEditorDialog key={dialog.id} mode="edit" definitionId={dialog.id} onClose={onClose} onSaved={(n) => { onNotice(n); onClose(); }} />;
  }
  if (dialog.kind === 'archive') {
    return <ArchiveDefinitionDialog key={dialog.id} definitionId={dialog.id} onClose={onClose} onArchived={(n) => { onNotice(n); onClose(); }} />;
  }
  return (
    <DefinitionDetailDialog
      key={dialog.id}
      definitionId={dialog.id}
      offerEdit={!!listEditability && anyEditAllowed(listEditability)}
      offerArchive={!!listEditability?.can_archive}
      onClose={onClose}
      onEdit={() => setDialog({ kind: 'edit', id: dialog.id })}
      onArchive={() => setDialog({ kind: 'archive', id: dialog.id })}
    />
  );
}

function Toolbar({
  tab,
  setTab,
  q,
  setQ,
  disabled,
  counts,
}: {
  tab: TabKey;
  setTab: (tab: TabKey) => void;
  q: string;
  setQ: (q: string) => void;
  disabled: boolean;
  counts: { active: number; archived: number } | null;
}): JSX.Element {
  const { t } = useTranslation();
  return (
    <div className="flex min-w-0 flex-col gap-3 sm:flex-row sm:flex-wrap sm:items-end">
      <Tabs value={tab} onValueChange={(v) => setTab(v as TabKey)}>
        <TabsList variant="underline" aria-label={t('settings.taskAttributes.list.tabsLabel')} className="text-sm">
          <TabsTrigger variant="underline" value="active" disabled={disabled} className="data-[state=active]:text-accent-text text-sm">
            {t('settings.taskAttributes.list.tabActive')}
            {counts && <span className="text-text-muted ml-1.5 tabular-nums">{counts.active}</span>}
          </TabsTrigger>
          <TabsTrigger variant="underline" value="archived" disabled={disabled} className="data-[state=active]:text-accent-text text-sm">
            {t('settings.taskAttributes.list.tabArchived')}
            {counts && <span className="text-text-muted ml-1.5 tabular-nums">{counts.archived}</span>}
          </TabsTrigger>
        </TabsList>
      </Tabs>
      <div className="relative min-w-0 flex-1 sm:max-w-xs">
        <Search size={14} aria-hidden="true" className="text-text-muted pointer-events-none absolute top-1/2 left-3 -translate-y-1/2" />
        <label htmlFor="task-attributes-search" className="sr-only">{t('settings.taskAttributes.list.search')}</label>
        <Input
          id="task-attributes-search"
          type="search"
          className="pl-8"
          placeholder={t('settings.taskAttributes.list.search')}
          value={q}
          disabled={disabled}
          onChange={(e) => setQ(e.target.value)}
        />
      </div>
    </div>
  );
}

type RowAction = { state: 'pending' } | { state: 'enabled' } | { state: 'refused'; code: string | null } | { state: 'unavailable' };

function rowAction(read: TaskAttributesQuery<AttributeDefinitionRead> | undefined, allowed: (e: AttributeDefinitionEditability) => boolean): RowAction {
  if (!read || (read.isError && !read.isFetching)) return read?.isError ? { state: 'unavailable' } : { state: 'pending' };
  if (!read.isSuccess || read.isFetching || !read.data) return { state: 'pending' };
  const e = read.data.editability;
  return allowed(e) ? { state: 'enabled' } : { state: 'refused', code: e.refusal_reason?.code ?? null };
}

function DefinitionRow({
  definition: d,
  duplicateOf,
  offerEdit,
  offerArchive,
  read,
  paused,
  onOpen,
  onEdit,
  onArchive,
}: {
  definition: AttributeDefinition;
  duplicateOf: string | null;
  offerEdit: boolean;
  offerArchive: boolean;
  read: TaskAttributesQuery<AttributeDefinitionRead> | undefined;
  paused: boolean;
  onOpen: (el: HTMLElement) => void;
  onEdit: (el: HTMLElement) => void;
  onArchive: (el: HTMLElement) => void;
}): JSX.Element {
  const { t } = useTranslation();
  const id = d.definition_id;
  const edit = rowAction(read, anyEditAllowed);
  const archive = rowAction(read, (e) => e.can_archive);
  const reasonId = `ta-row-reason-${id}`;
  const pending = (offerEdit && edit.state === 'pending') || (offerArchive && archive.state === 'pending');
  const unavailable = edit.state === 'unavailable' || archive.state === 'unavailable';
  const refusedCode = edit.state === 'refused' ? edit.code : archive.state === 'refused' ? archive.code : undefined;
  const showRefusal = (offerEdit && edit.state === 'refused') || (offerArchive && archive.state === 'refused');
  const describedBy = pending || unavailable || showRefusal ? reasonId : undefined;

  return (
    <li className="border-border-default flex min-w-0 flex-col gap-2 border-b p-4 last:border-b-0" data-testid={`ta-row-${d.key}`}>
      <Overline>{t('settings.taskAttributes.list.colName')}</Overline>
      <div className="flex min-w-0 flex-wrap items-center gap-2">
        <span className="text-text-primary font-semibold break-words">{d.name}</span>
        <KeyChip value={d.key} />
        {duplicateOf && <DupNameBadge otherKey={duplicateOf} />}
      </div>
      {d.description && <p className="text-text-secondary line-clamp-2 text-sm break-words whitespace-pre-wrap">{d.description}</p>}
      <Overline>{t('settings.taskAttributes.list.colType')}</Overline>
      <p className="text-sm break-words">
        <strong className="font-semibold">{typeLabel(t, d.type)}</strong>
        {' · '}
        {constraintSummary(t, d)}
      </p>
      <div className="flex min-w-0 flex-wrap items-center gap-2 text-sm">
        <Overline>{t('settings.taskAttributes.list.colState')}</Overline>
        <StateBadge archived={d.archived} />
        {d.first_used_at === null && <UnusedBadge />}
        <span className="text-text-secondary">{t('settings.taskAttributes.list.version', { v: d.version })}</span>
      </div>
      <div className="flex flex-wrap items-center gap-2" role="group" aria-label={t('settings.taskAttributes.list.rowActions', { name: d.name })}>
        <Button variant="ghost" size="sm" onClick={(e) => onOpen(e.currentTarget)}>{t('settings.taskAttributes.list.open')}</Button>
        {offerEdit && (
          <Button
            variant="outline"
            size="sm"
            disabled={paused || edit.state !== 'enabled'}
            aria-busy={edit.state === 'pending' || undefined}
            aria-describedby={describedBy}
            onClick={(e) => onEdit(e.currentTarget)}
          >
            {t('settings.taskAttributes.list.edit')}
          </Button>
        )}
        {offerArchive && (
          <Button
            variant="ghost"
            size="sm"
            disabled={paused || archive.state !== 'enabled'}
            aria-busy={archive.state === 'pending' || undefined}
            aria-describedby={describedBy}
            onClick={(e) => onArchive(e.currentTarget)}
          >
            {t('settings.taskAttributes.list.archive')}
          </Button>
        )}
        {unavailable && (
          <Button variant="outline" size="sm" onClick={() => void read?.refetch()}>{t('settings.taskAttributes.list.retry')}</Button>
        )}
      </div>
      {pending && <p id={reasonId} className="sr-only">{t('settings.taskAttributes.list.actionsChecking')}</p>}
      {!pending && unavailable && <p id={reasonId} className="text-text-secondary text-xs">{t('settings.taskAttributes.list.actionsUnavailable')}</p>}
      {!pending && !unavailable && showRefusal && <p id={reasonId} className="text-text-secondary text-xs">{t(refusalKey(refusedCode))}</p>}
    </li>
  );
}

/** definition_id → the key of another definition with the same display name. */
function duplicateNames(definitions: AttributeDefinition[]): Map<string, string> {
  const byName = new Map<string, AttributeDefinition[]>();
  for (const d of definitions) {
    const name = d.name.trim().toLowerCase();
    byName.set(name, [...(byName.get(name) ?? []), d]);
  }
  const result = new Map<string, string>();
  for (const group of byName.values()) {
    if (group.length < 2) continue;
    for (const d of group) {
      const other = group.find((x) => x.definition_id !== d.definition_id);
      if (other) result.set(d.definition_id, other.key);
    }
  }
  return result;
}
