/**
 * Edit task attributes (THR-390 PR7, Screen E).
 *
 * - Authority comes only from this dialog's own fresh read (re-read on mount,
 *   never the card's cache; MEM-300), and is rechecked when Save is pressed.
 * - One Save sends ONE PUT holding only the changed, writable fields, each
 *   with its read `expected_version` / `expected_revision` (per-field CAS).
 * - Any refusal is whole-save: nothing was written. 409 keeps the draft and
 *   offers Reload latest; 422 maps `changes.<i>.value` back to fields; 403
 *   switches to the same server refusal copy as the card; a lost response is
 *   "Save outcome unknown" until Refresh to check. Never an automatic retry.
 * - A field a conflict reports archived stays in the dialog read-only with
 *   "Your draft: … · not saved" until the dialog closes (Reload latest never
 *   re-enables it, even when the ordinary read omits it).
 * - No status-derived rule: only `editability` from the server (correction 3).
 */
import { useEffect, useRef, useState } from 'react';
import { useParams } from 'react-router-dom';
import { Button } from '@/design-system/primitives/Button';
import { Dialog, DialogContent, DialogDescription, DialogTitle } from '@/design-system/primitives/Dialog';
import { Input } from '@/design-system/primitives/Input';
import { useTranslation } from '@/hooks/i18n';
import { useTaskAttributeValueActions, useTaskAttributes } from '@/hooks/task-attribute-values';
import type { AttributeChange, TaskAttributeRead, TaskAttributesRead } from '@/lib/api/types';
import { useConfigOwner, useOwnerGate } from '@/shared/owners';
import {
  VALUE_ERROR_KEY, archivedInConflict, buildChanges, classifySaveError, draftClipboardText, draftOf,
  fieldChanged, fieldWritable, isArchivedField, latestEvent, type Draft, type FieldDraft, type FieldMode, type SaveFailure,
} from './model';
import { ArchivedBadge, KeyChip, Note, ProvenanceBadge, RefusalNotice, ValueText, constraintSummary, formatWhen, valueString, useActorLabel } from './parts';

type Status =
  | { s: 'idle' }
  | { s: 'saving' }
  | { s: 'validation'; errors: Record<string, string[]> }
  | { s: 'conflict'; failure: Extract<SaveFailure, { kind: 'conflict' }>; submitted: AttributeChange[]; base: TaskAttributesRead }
  | { s: 'refused'; code: string }
  | { s: 'failed' }
  | { s: 'unknown'; checking: boolean; refreshFailed: boolean };

interface Retained {
  attr: TaskAttributeRead;
  draft: FieldDraft;
}

const fieldId = (definitionId: string): string => `ta-v-${definitionId}`;

export function TaskAttributesEditorDialog({
  taskId, taskTitle, onClose, onSaved,
}: {
  taskId: string;
  taskTitle: string;
  onClose: () => void;
  onSaved: (read: TaskAttributesRead) => void;
}): JSX.Element {
  const { t, locale } = useTranslation();
  const { slug } = useParams<{ slug: string }>();
  const query = useTaskAttributes(taskId, true);
  const actions = useTaskAttributeValueActions(taskId);
  const actorLabel = useActorLabel();
  const openedAt = useRef(Date.now());
  const [base, setBase] = useState<TaskAttributesRead | null>(null);
  const [draft, setDraft] = useState<Draft>({});
  const [status, setStatus] = useState<Status>({ s: 'idle' });
  const [retained, setRetained] = useState<Record<string, Retained>>({});
  const [confirmDiscard, setConfirmDiscard] = useState(false);
  const [copied, setCopied] = useState(false);
  const sent = useRef<AttributeChange[] | null>(null);
  const summaryRef = useRef<HTMLDivElement | null>(null);
  const titleRef = useRef<HTMLHeadingElement | null>(null);

  // The first settled read fetched since this dialog opened is its authority.
  useEffect(() => {
    if (base || !query.isSuccess || query.isFetching || !query.data || query.dataUpdatedAt < openedAt.current) return;
    setBase(query.data);
    setDraft(draftOf(query.data));
  }, [base, query.isSuccess, query.isFetching, query.data, query.dataUpdatedAt]);

  useEffect(() => {
    if (status.s === 'validation') summaryRef.current?.focus();
  }, [status]);

  const changedIds = base ? base.attributes.filter((a) => fieldWritable(base, a) && fieldChanged(a, draft[a.definition.definition_id])).map((a) => a.definition.definition_id) : [];
  const dirty = changedIds.length > 0 || Object.keys(retained).length > 0;

  const owner = useConfigOwner({
    page: 'tasks.attributes',
    subject: taskId,
    org: slug || null,
    dirty,
    draft,
    onDiscard: onClose,
    checkResult: () => refreshRef.current(),
  });
  const { requestOwnerTransition } = useOwnerGate();

  const refusedCode = status.s === 'refused' ? status.code : null;
  const shownRead: TaskAttributesRead | null = base && refusedCode
    ? { ...base, editability: { can_edit: false, refusal_reason: { code: refusedCode } } }
    : base;
  const canEdit = !!shownRead?.editability.can_edit;
  const locked = !canEdit || status.s === 'saving' || status.s === 'unknown' || owner.busy;
  const needsReload = status.s === 'conflict';
  const anyWritable = !!base && base.attributes.some((a) => fieldWritable(base, a));
  const saveDisabled = !base || locked || needsReload || changedIds.length === 0;

  const setField = (id: string, next: Partial<FieldDraft>): void => {
    setDraft((d) => ({ ...d, [id]: { ...(d[id] ?? { mode: 'unset', raw: '' }), ...next } }));
    if (status.s === 'validation' || status.s === 'failed') setStatus({ s: 'idle' });
  };

  const handleFailure = (error: unknown, submitted: AttributeChange[]): void => {
    const failure = classifySaveError(error, submitted);
    if (!base) return;
    switch (failure.kind) {
      case 'unknown':
        return setStatus({ s: 'unknown', checking: false, refreshFailed: false });
      case 'failed':
        return setStatus({ s: 'failed' });
      case 'validation':
        return setStatus({ s: 'validation', errors: failure.errors });
      case 'refused':
        // The card re-reads so it shows the same server refusal state.
        void actions.read().catch(() => undefined);
        return setStatus({ s: 'refused', code: failure.code });
      case 'conflict': {
        const archived = archivedInConflict(failure.current, submitted);
        if (archived.length) {
          setRetained((prev) => {
            const next = { ...prev };
            for (const id of archived) {
              const attr = failure.current.attributes.find((a) => a.definition.definition_id === id);
              const d = draft[id];
              if (attr && d) next[id] = { attr, draft: d };
            }
            return next;
          });
        }
        return setStatus({ s: 'conflict', failure, submitted, base });
      }
    }
  };

  const save = async (): Promise<void> => {
    if (saveDisabled || !base) return;
    // MEM-300: recheck at the callback that the authority read is settled.
    if (!actions.isCurrent()) return;
    const built = buildChanges(base, draft);
    if (built.parseErrors.length) {
      setStatus({ s: 'validation', errors: Object.fromEntries(built.parseErrors.map((id) => [id, ['expected_finite_number']])) });
      return;
    }
    if (!built.changes.length) return;
    const submitted = built.changes;
    sent.current = submitted;
    setStatus({ s: 'saving' });
    const result = await owner.track(
      () => actions.save(submitted),
      (error) => (classifySaveError(error, submitted).kind === 'unknown' ? 'unknown' : 'failed'),
    );
    if (result.ok) {
      actions.accept(result.value);
      onSaved(result.value);
      onClose();
      return;
    }
    if (result.refused) return setStatus(owner.saveRefused ? { s: 'unknown', checking: false, refreshFailed: false } : { s: 'idle' });
    if ((result.error as Error | null)?.name === 'OwnersReceiptError') return setStatus({ s: 'failed' });
    handleFailure(result.error, submitted);
  };

  /** Adopt a fresh read; keep drafts whose field revision did not move. */
  const adopt = (latest: TaskAttributesRead, keepAll: boolean): void => {
    const prior = base;
    setBase(latest);
    setDraft((d) => {
      const next = draftOf(latest);
      for (const a of latest.attributes) {
        const id = a.definition.definition_id;
        const before = prior?.attributes.find((x) => x.definition.definition_id === id);
        if (d[id] && (keepAll || (before && before.current.revision === a.current.revision))) next[id] = d[id] as FieldDraft;
      }
      return next;
    });
  };

  const reloadLatest = async (): Promise<void> => {
    try {
      const latest = await actions.read();
      adopt(latest, false);
      setStatus({ s: 'idle' });
    } catch {
      // The refusal stays shown; nothing was sent.
    }
  };

  // Same-context reconciliation of a lost response (also the owner's Check result).
  const refreshToCheck = async (): Promise<'saved' | 'not_saved' | 'unknown'> => {
    const submitted = sent.current;
    setStatus({ s: 'unknown', checking: true, refreshFailed: false });
    try {
      const latest = await actions.read();
      const committed = !!submitted && submitted.every((c) => {
        const a = latest.attributes.find((x) => x.definition.definition_id === c.definition_id);
        if (!a) return false;
        return c.op === 'clear' ? !a.current.present : a.current.present && a.current.value === c.value;
      });
      adopt(latest, true);
      setStatus({ s: 'idle' });
      return committed ? 'saved' : 'not_saved';
    } catch {
      setStatus({ s: 'unknown', checking: false, refreshFailed: true });
      return 'unknown';
    }
  };
  const refreshRef = useRef(refreshToCheck);
  refreshRef.current = refreshToCheck;

  const copyDraft = (): void => {
    if (!base) return;
    void navigator.clipboard?.writeText(draftClipboardText(base, draft)).then(() => setCopied(true), () => setCopied(false));
  };

  const ownerHolds = (): boolean => owner.isBusy() || owner.record?.status === 'pending' || owner.record?.status === 'unknown';
  const closeThroughGate = (): void => {
    requestOwnerTransition({ effect: 'close', affects: (record) => record.id === owner.id, proceed: onClose });
  };
  const requestClose = (): void => {
    if (!ownerHolds() && dirty) {
      setConfirmDiscard(true);
      return;
    }
    closeThroughGate();
  };
  const discardAndClose = (): void => {
    setConfirmDiscard(false);
    if (ownerHolds()) return closeThroughGate();
    onClose();
  };

  // Rows: the read's fields, plus conflict-retained archived fields it omits.
  const rows: { a: TaskAttributeRead; retained: Retained | null }[] = base
    ? [
      ...base.attributes.map((a) => ({ a: retained[a.definition.definition_id]?.attr ?? a, retained: retained[a.definition.definition_id] ?? null })),
      ...Object.values(retained).filter((r) => !base.attributes.some((a) => a.definition.definition_id === r.attr.definition.definition_id)).map((r) => ({ a: r.attr, retained: r })),
    ]
    : [];
  const errors = status.s === 'validation' ? status.errors : {};
  const errorIds = Object.keys(errors).filter((id) => rows.some((r) => r.a.definition.definition_id === id));
  const errorText = (id: string): string => (errors[id] ?? []).map((c) => t(VALUE_ERROR_KEY[c] ?? 'tasks.attributes.err.other')).join(' ');

  const loadFailed = !base && query.isError && !query.isFetching;

  return (
    <Dialog open onOpenChange={(next) => { if (!next) requestClose(); }}>
      <DialogContent
        className="flex max-w-2xl min-w-0 flex-col gap-0 overflow-hidden p-0"
        closeLabel={t('tasks.attributes.editor.cancel')}
        aria-describedby="ta-editor-sub"
        onOpenAutoFocus={(e) => { e.preventDefault(); titleRef.current?.focus(); }}
      >
        <div className="px-6 pt-6 pr-12 pb-3">
          <DialogTitle ref={titleRef} tabIndex={-1} className="font-display text-lg outline-none">{t('tasks.attributes.editor.title')}</DialogTitle>
          <DialogDescription id="ta-editor-sub" className="mt-1 font-mono text-xs break-words">{taskId} · {taskTitle}</DialogDescription>
        </div>
        <div className="min-h-0 flex-1 overflow-y-auto px-6 pt-1 pb-4" data-testid="ta-editor-body">
          {!base ? (
            loadFailed ? (
              <Note tone="danger" role="alert" actions={<Button size="sm" variant="outline" className="bg-surface-raised" onClick={() => void query.refetch()}>{t('tasks.attributes.retry')}</Button>}>
                {t('tasks.attributes.card.fetchError')}
              </Note>
            ) : (
              <div aria-busy="true" className="flex flex-col gap-3">
                <span className="sr-only">{t('tasks.attributes.card.loading')}</span>
                {[0, 1, 2].map((i) => <div key={i} aria-hidden="true" className="bg-surface-sunken h-10 rounded-md" />)}
              </div>
            )
          ) : (
            <form noValidate className="flex min-w-0 flex-col gap-3" onSubmit={(e) => { e.preventDefault(); void save(); }}>
              <p className="text-text-secondary text-sm">{t('tasks.attributes.editor.intro')}</p>
              {shownRead && <RefusalNotice read={shownRead} />}
              {canEdit && !anyWritable && <Note tone="info" role="status" testId="ta-no-active">{t('tasks.attributes.editor.noActive')}</Note>}
              {errorIds.length > 0 && (
                <div ref={summaryRef} role="alert" tabIndex={-1} className="border-status-escalated/30 bg-tier-red-tint text-status-escalated rounded-md border px-3 py-2.5 text-xs outline-none" data-testid="ta-error-summary">
                  <strong className="font-semibold">{t('tasks.attributes.editor.errSummary', { count: errorIds.length })}</strong>
                  <ul className="mt-1 list-disc pl-5">
                    {errorIds.map((id) => {
                      const a = rows.find((r) => r.a.definition.definition_id === id)?.a;
                      return (
                        <li key={id}>
                          <a href={`#${fieldId(id)}`} className="underline" onClick={(e) => { e.preventDefault(); document.getElementById(fieldId(id))?.focus(); }}>
                            {a?.definition.name}: {errorText(id)}
                          </a>
                        </li>
                      );
                    })}
                  </ul>
                </div>
              )}
              {status.s === 'conflict' && (
                <ConflictNotice status={status} actorLabel={actorLabel} />
              )}
              {status.s === 'failed' && <Note tone="danger" role="alert">{t('tasks.attributes.editor.networkFail')}</Note>}
              {status.s === 'unknown' && (
                <Note
                  tone="attention"
                  role="alert"
                  title={`${t('tasks.attributes.editor.unknownTitle')}.`}
                  actions={<Button type="button" size="sm" variant="outline" className="bg-surface-raised" loading={status.checking} onClick={() => void owner.check().catch(() => undefined)}>{t('tasks.attributes.editor.refresh')}</Button>}
                >
                  {t('tasks.attributes.editor.unknownBody')}
                  {status.refreshFailed ? ` ${t('tasks.attributes.editor.refreshFailed')}` : ''}
                </Note>
              )}
              {rows.map(({ a, retained: r }) => (
                <EditorRow
                  key={a.definition.definition_id}
                  read={base}
                  a={a}
                  retained={r}
                  draft={r ? r.draft : draft[a.definition.definition_id]}
                  locked={locked}
                  errors={errors[a.definition.definition_id] ? errorText(a.definition.definition_id) : ''}
                  conflict={status.s === 'conflict' ? status : null}
                  onChange={(next) => setField(a.definition.definition_id, next)}
                  locale={locale}
                />
              ))}
              <button type="submit" hidden tabIndex={-1} aria-hidden="true" />
            </form>
          )}
        </div>
        <div className="border-border-default flex flex-wrap items-center justify-end gap-2 border-t px-6 py-3">
          {copied && <span role="status" className="text-text-secondary mr-auto text-xs">{t('tasks.attributes.editor.copied')}</span>}
          {status.s === 'failed' && !copied && <span className="text-text-secondary mr-auto text-xs">{t('tasks.attributes.editor.keepDraft')}</span>}
          {base && !locked && changedIds.length === 0 && !needsReload && (
            <span className="text-text-secondary mr-auto text-xs" data-testid="ta-no-changes">{t('tasks.attributes.editor.noChange')}</span>
          )}
          {(needsReload || status.s === 'refused') && (
            <Button variant="outline" className="bg-surface-raised" onClick={copyDraft}>{t('tasks.attributes.editor.copyDraft')}</Button>
          )}
          {needsReload && (
            <Button variant="outline" className="bg-surface-raised" onClick={() => void reloadLatest()}>{t('tasks.attributes.editor.reload')}</Button>
          )}
          <Button variant="ghost" onClick={requestClose} disabled={status.s === 'saving'}>{t('tasks.attributes.editor.cancel')}</Button>
          <Button onClick={() => void save()} disabled={saveDisabled} loading={status.s === 'saving'} data-testid="ta-save">
            {status.s === 'saving' ? t('tasks.attributes.editor.saving') : status.s === 'failed' ? t('tasks.attributes.editor.tryAgain') : t('tasks.attributes.editor.save')}
          </Button>
        </div>
      </DialogContent>
      <Dialog open={confirmDiscard} onOpenChange={setConfirmDiscard}>
        <DialogContent className="max-w-sm" closeLabel={t('tasks.attributes.editor.keepEditing')} onOpenAutoFocus={(e) => {
          e.preventDefault();
          (document.getElementById('ta-v-keep-editing') as HTMLButtonElement | null)?.focus();
        }}>
          <DialogTitle className="font-display text-lg">{t('tasks.attributes.editor.discardTitle')}</DialogTitle>
          <DialogDescription>{t('tasks.attributes.editor.discardBody')}</DialogDescription>
          <div className="flex flex-wrap justify-end gap-2">
            <Button id="ta-v-keep-editing" variant="outline" className="bg-surface-raised" onClick={() => setConfirmDiscard(false)}>{t('tasks.attributes.editor.keepEditing')}</Button>
            <Button variant="destructive" onClick={discardAndClose}>{t('tasks.attributes.editor.discard')}</Button>
          </div>
        </DialogContent>
      </Dialog>
    </Dialog>
  );
}

function ConflictNotice({
  status, actorLabel,
}: {
  status: Extract<Status, { s: 'conflict' }>;
  actorLabel: (id: string, kind: string) => string;
}): JSX.Element {
  const { t, locale } = useTranslation();
  const { failure, submitted } = status;
  const archived = archivedInConflict(failure.current, submitted);
  if (archived.length) {
    const names = failure.current.attributes.filter((a) => archived.includes(a.definition.definition_id)).map((a) => a.definition.name).join(', ');
    return <Note tone="danger" role="alert" testId="ta-conflict">{t('tasks.attributes.editor.archivedDuring', { name: names })}</Note>;
  }
  if (failure.code === 'value_revision_conflict' && failure.conflicts?.length) {
    const ids = failure.conflicts.map((c) => c.definition_id);
    const attrs = failure.current.attributes.filter((a) => ids.includes(a.definition.definition_id));
    const ev = attrs.map((a) => latestEvent(a)).filter((e) => e !== null).sort((x, y) => y.at.localeCompare(x.at))[0];
    return (
      <div className="flex min-w-0 flex-col gap-2" data-testid="ta-conflict">
        <Note tone="attention" role="alert" title={`${t('tasks.attributes.editor.conflictTitle')}.`}>
          {ev
            ? t('tasks.attributes.editor.conflictBody', { actor: actorLabel(ev.actor_id, ev.actor_kind), time: formatWhen(locale, ev.at) })
            : t('tasks.attributes.editor.conflictBodyAnon')}
        </Note>
        <div className="grid min-w-0 grid-cols-1 gap-2 sm:grid-cols-2" data-testid="ta-compare">
          {(['draft', 'latest'] as const).map((side) => (
            <div key={side} className="border-border-default bg-surface-raised min-w-0 rounded border px-3 py-2 text-xs">
              <div className="text-text-muted mb-1 text-overline font-bold tracking-wider uppercase">
                {side === 'draft' ? t('tasks.attributes.editor.yourDraft') : t('tasks.attributes.editor.latest')}
              </div>
              <dl className="grid grid-cols-2 gap-x-2.5 gap-y-1">
                {attrs.map((a) => {
                  const c = submitted.find((x) => x.definition_id === a.definition.definition_id);
                  const latestEv = latestEvent(a);
                  return [
                    <dt key={`${a.definition.definition_id}-k`} className="text-text-muted font-mono">{a.definition.key}</dt>,
                    <dd key={`${a.definition.definition_id}-v`} className="m-0 min-w-0 break-words">
                      {side === 'draft'
                        ? <span className="bg-attention-soft text-attention-text rounded px-1">{valueString(t, a.definition.type, c && c.op === 'set' ? { present: true, value: c.value } : { present: false })}</span>
                        : <>{valueString(t, a.definition.type, a.current)}{latestEv ? ` · ${actorLabel(latestEv.actor_id, latestEv.actor_kind)} · ${formatWhen(locale, latestEv.at)}` : ''}</>}
                    </dd>,
                  ];
                })}
              </dl>
            </div>
          ))}
        </div>
      </div>
    );
  }
  return <Note tone="attention" role="alert" testId="ta-conflict">{t('tasks.attributes.editor.definitionChanged')}</Note>;
}

function EditorRow({
  read, a, retained, draft, locked, errors, conflict, onChange, locale,
}: {
  read: TaskAttributesRead;
  a: TaskAttributeRead;
  retained: Retained | null;
  draft: FieldDraft | undefined;
  locked: boolean;
  errors: string;
  conflict: Extract<Status, { s: 'conflict' }> | null;
  onChange: (next: Partial<FieldDraft>) => void;
  locale: ReturnType<typeof useTranslation>['locale'];
}): JSX.Element {
  const { t } = useTranslation();
  const d = a.definition;
  const id = fieldId(d.definition_id);
  const archived = !!retained || isArchivedField(a);
  const writable = !archived && fieldWritable(read, a);
  const disabled = locked || !writable;
  const dr = draft ?? { mode: 'unset' as FieldMode, raw: '' };
  const changed = !archived && fieldChanged(a, draft);
  const initial = a.initial.state === 'set' ? a.initial : null;
  const latestDef = conflict?.failure.current.attributes.find((x) => x.definition.definition_id === d.definition_id)?.definition;
  const versionMoved = !!latestDef && !latestDef.archived && conflict?.submitted.some((c) => c.definition_id === d.definition_id && c.expected_version !== latestDef.version);
  const modes: [FieldMode, string][] = d.type === 'boolean'
    ? [['unset', t('tasks.attributes.editor.unset')], ['true', t('tasks.attributes.value.true')], ['false', t('tasks.attributes.value.false')]]
    : [['unset', t('tasks.attributes.editor.unset')], ['set', t('tasks.attributes.editor.set')]];

  return (
    <div
      className={`min-w-0 rounded-md border px-3.5 py-3 ${archived ? 'border-border-default bg-surface-sunken' : changed ? 'border-accent-text/45 bg-surface-raised' : 'border-border-default bg-surface-raised'}`}
      data-testid={`ta-edit-${d.key}`}
    >
      <div className="flex min-w-0 flex-wrap items-center gap-2">
        <span className="text-text-primary text-sm font-semibold break-words" id={`${id}-n`}>{d.name}</span>
        <KeyChip>{d.key}</KeyChip>
        {archived && <ArchivedBadge readOnly />}
        <span className="text-text-muted ml-auto text-xs">{constraintSummary(t, d)}</span>
      </div>
      {archived ? (
        <>
          <div className="mt-2"><ValueText type={d.type} value={a.current} /></div>
          {retained && a.definition.archived_at && (
            <div className="text-text-muted mt-1.5 text-xs">{t('tasks.attributes.editor.archivedBy', { time: formatWhen(locale, a.definition.archived_at) })}</div>
          )}
          {retained && (
            <div className="mt-1.5 text-xs">
              <span className="bg-attention-soft text-attention-text rounded-full px-2 py-0.5 font-semibold" data-testid="ta-draft-not-saved">
                {t('tasks.attributes.editor.draftNotSaved', { value: retained.draft.mode === 'unset' ? t('tasks.attributes.value.notSet') : d.type === 'boolean' ? (retained.draft.mode === 'true' ? t('tasks.attributes.value.true') : t('tasks.attributes.value.false')) : retained.draft.raw })}
              </span>
            </div>
          )}
        </>
      ) : (
        <>
          <fieldset className="mt-2.5 min-w-0 border-0 p-0" aria-describedby={errors ? `${id}-err` : undefined}>
            <legend className="sr-only">{d.name}</legend>
            <div className="border-border-strong inline-flex flex-wrap overflow-hidden rounded border">
              {modes.map(([mode, label]) => (
                <label
                  key={mode}
                  className={`border-border-strong relative inline-flex min-h-9 cursor-pointer items-center border-r px-3.5 text-sm font-semibold last:border-r-0 has-[:disabled]:cursor-not-allowed has-[:disabled]:opacity-60 has-[:focus-visible]:outline-2 has-[:focus-visible]:-outline-offset-2 ${dr.mode === mode ? 'bg-accent-soft text-accent-text' : 'bg-surface-raised text-text-secondary'}`}
                >
                  <input
                    type="radio"
                    name={`${id}-mode`}
                    value={mode}
                    checked={dr.mode === mode}
                    disabled={disabled}
                    onChange={() => onChange({ mode })}
                    className="absolute inset-0 m-0 cursor-pointer opacity-0"
                  />
                  {label}
                </label>
              ))}
            </div>
          </fieldset>
          {d.type !== 'boolean' && (
            <div className="mt-2 flex min-w-0 flex-col gap-1">
              <label className="sr-only" htmlFor={id}>{t('tasks.attributes.editor.valueLabel', { name: d.name })}</label>
              <Input
                id={id}
                className={d.type === 'numeric' ? 'font-mono' : undefined}
                inputMode={d.type === 'numeric' ? 'decimal' : undefined}
                value={dr.mode === 'set' ? dr.raw : ''}
                disabled={disabled || dr.mode !== 'set'}
                autoComplete="off"
                spellCheck={false}
                aria-invalid={errors ? true : undefined}
                aria-describedby={errors ? `${id}-h ${id}-err` : `${id}-h`}
                onChange={(e) => onChange({ raw: e.target.value })}
              />
              <span id={`${id}-h`} className="text-text-muted text-xs">
                {d.type === 'numeric' ? constraintSummary(t, d) : t('tasks.attributes.editor.emptyAllowed')}
              </span>
            </div>
          )}
          {errors && <p id={`${id}-err`} className="text-status-escalated mt-1 text-xs font-medium">{errors}</p>}
          {a.current.present && dr.mode === 'unset' && (
            <div role="status" className="text-text-muted mt-2 text-xs">
              {t('tasks.attributes.editor.clearNote', { key: d.key, old: valueString(t, d.type, a.current) })}
            </div>
          )}
          {versionMoved && latestDef && (
            <div className="text-attention-text mt-2 text-xs font-semibold" data-testid="ta-now-version">
              {t('tasks.attributes.editor.nowVersion', { v: latestDef.version, name: latestDef.name })}
            </div>
          )}
        </>
      )}
      <div className="text-text-muted mt-2 flex min-w-0 flex-wrap items-center gap-1.5 text-xs">
        {initial ? (
          <>
            <ProvenanceBadge provenance="supplied_at_admission" />
            <span className="text-text-secondary font-mono">{valueString(t, d.type, { present: true, value: initial.value })}</span>
            <span>· {t('tasks.attributes.editor.immutable')}</span>
          </>
        ) : (
          <ProvenanceBadge provenance={a.provenance} />
        )}
      </div>
    </div>
  );
}
