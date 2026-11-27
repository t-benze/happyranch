/**
 * Create / edit a task-attribute definition (THR-390 PR6, Screens B and B2).
 *
 * - Edit authority comes only from this dialog's own fresh `getDefinition`
 *   (never the list or a cached row prefetch): Save is gated on that read
 *   being a settled success fetched since the dialog opened (MEM-300), and
 *   rechecked when Save is pressed. Structure fields lock from the server's
 *   `can_edit_structure`, never from `first_used_at`.
 * - Refusals render `detail.current` exactly; nothing is reconstructed.
 *   `definition_structure_frozen` (correction 4) and an archive race
 *   (correction 5) keep the draft and never report success.
 * - A keyless create lost response remains unknown after Check list. Explicit
 *   acknowledgement closes only this owner without claiming Saved/Not saved.
 *   Edit retains stable-ID/full-field reconciliation and definite-failure Retry.
 * - "Reload latest" always re-reads by `definition_id`.
 */
import { useEffect, useRef, useState, type ReactNode } from 'react';
import { useParams } from 'react-router-dom';
import { AlertCircle } from 'lucide-react';
import { Button } from '@/design-system/primitives/Button';
import { Dialog, DialogContent, DialogDescription, DialogTitle } from '@/design-system/primitives/Dialog';
import { Input } from '@/design-system/primitives/Input';
import { Textarea } from '@/design-system/primitives/Textarea';
import { useTranslation } from '@/hooks/i18n';
import { useIdentityPresentation } from '@/hooks/identities';
import { useDefinition, useTaskAttributeActions } from '@/hooks/task-attributes';
import type { AttributeDefinition, AttributeDefinitionEditability, AttributeDefinitionCreateFields, AttributeDefinitionFields, AttributeDefinitionRead } from '@/lib/api/types';
import type { MessageKey } from '@/lib/i18n';
import { useOwnerGate, useConfigOwner } from '@/shared/owners';
import {
  ATTRIBUTE_TYPES,
  charLength,
  classifyWriteError,
  currentDefinitionRead,
  draftClipboardText,
  echoReflects,
  fieldsOf,
  reconcileEdit,
  type DefinitionFields,
} from './model';
import { Callout, LockedRow, StateBadge, TYPE_KEY, constraintSummary, formatWhen, typeLabel } from './parts';
import type { ListNotice } from './TaskAttributesSection';

interface Draft {
  name: string;
  key: string;
  type: string;
  description: string;
  min: string;
  max: string;
  integerOnly: boolean;
  minLen: string;
  maxLen: string;
}

const EMPTY: Draft = { name: '', key: '', type: '', description: '', min: '', max: '', integerOnly: false, minLen: '', maxLen: '' };
const num = (v: number | boolean | undefined): string => (typeof v === 'number' ? String(v) : '');

function draftOf(d: AttributeDefinition): Draft {
  const c = d.constraints;
  return {
    name: d.name, key: d.key, type: d.type, description: d.description ?? '',
    min: num(c.min), max: num(c.max), integerOnly: c.integer_only === true, minLen: num(c.min_length), maxLen: num(c.max_length),
  };
}

const parse = (s: string): number | undefined => {
  if (s.trim() === '') return undefined;
  const n = Number(s);
  return Number.isFinite(n) ? n : undefined;
};

/**
 * F2: a whole-number bound outside the JS safe-integer range cannot be held
 * exactly (`Number` would silently round it), so it is refused before sending.
 */
const unsafeInteger = (v: unknown): boolean => {
  const n = typeof v === 'string' ? (v.trim() === '' ? NaN : Number(v)) : v;
  return typeof n === 'number' && Number.isInteger(n) && !Number.isSafeInteger(n);
};

const BOUND_PATHS = { min: 'constraints.min', max: 'constraints.max', minLen: 'constraints.min_length', maxLen: 'constraints.max_length' } as const;

/** Bounds that would be sent (or were changed) but cannot be represented exactly. */
function unsafeBoundErrors(draft: Draft, baseDraft: Draft, body: Record<string, unknown>): Record<string, string[]> {
  const sentConstraints = (body.constraints ?? {}) as Record<string, unknown>;
  const fields = draft.type === 'numeric' ? (['min', 'max'] as const) : draft.type === 'string' ? (['minLen', 'maxLen'] as const) : [];
  const errors: Record<string, string[]> = {};
  for (const f of fields) {
    const path = BOUND_PATHS[f];
    const sentValue = sentConstraints[path.slice('constraints.'.length)];
    if ((draft[f] !== baseDraft[f] && unsafeInteger(draft[f])) || unsafeInteger(sentValue)) errors[path] = ['unsafe_integer'];
  }
  return errors;
}

function constraintsOf(draft: Draft, base: Record<string, number | boolean> | null): Record<string, number | boolean> {
  const c: Record<string, number | boolean> = {};
  if (draft.type === 'numeric') {
    const min = parse(draft.min);
    const max = parse(draft.max);
    if (min !== undefined) c.min = min;
    if (max !== undefined) c.max = max;
    if (draft.integerOnly || (base && 'integer_only' in base)) c.integer_only = draft.integerOnly;
  } else if (draft.type === 'string') {
    const min = parse(draft.minLen);
    const max = parse(draft.maxLen);
    if (min !== undefined) c.min_length = min;
    if (max !== undefined) c.max_length = max;
  }
  return c;
}

/** The draft as definition fields (what the user means), for compare and Copy draft. */
function fieldsFromDraft(draft: Draft, base: AttributeDefinition | null): DefinitionFields {
  return {
    key: draft.key, type: draft.type, name: draft.name,
    description: draft.description === '' ? null : draft.description,
    constraints: constraintsOf(draft, base?.constraints ?? null),
  };
}

const sameJson = (a: unknown, b: unknown): boolean => JSON.stringify(a) === JSON.stringify(b);

type Status =
  | { s: 'idle' }
  | { s: 'saving' }
  | { s: 'validation'; errors: Record<string, string[]> }
  | { s: 'failed' }
  | { s: 'unknown'; checking: boolean; refreshFailed: boolean }
  | { s: 'notSaved' }
  | { s: 'keyReserved'; owner: AttributeDefinition; reconciled: boolean }
  | { s: 'conflict'; current: AttributeDefinitionRead }
  | { s: 'frozen'; current: AttributeDefinitionRead }
  | { s: 'archived'; current: AttributeDefinitionRead }
  | { s: 'echoMismatch'; current: AttributeDefinitionRead }
  | { s: 'permRevoked' }
  | { s: 'refused' };

interface Sent {
  body: Record<string, unknown>;
  draftKey: string;
  pre: AttributeDefinition | null;
}

const ERROR_KEY: Record<string, MessageKey> = {
  invalid_key: 'settings.taskAttributes.editor.err.invalidKey',
  key_generation_exhausted: 'settings.taskAttributes.editor.err.keyExhausted',
  length_1_80_trimmed: 'settings.taskAttributes.editor.err.nameLength',
  length_0_4000: 'settings.taskAttributes.editor.err.descLength',
  invalid_type: 'settings.taskAttributes.editor.err.invalidType',
  expected_object: 'settings.taskAttributes.editor.err.unsupported',
  unsupported_constraint: 'settings.taskAttributes.editor.err.unsupported',
  expected_boolean: 'settings.taskAttributes.editor.err.unsupported',
  expected_finite_number: 'settings.taskAttributes.editor.err.number',
  length_bound_0_4000: 'settings.taskAttributes.editor.err.lenBounds',
  inverted_bounds: 'settings.taskAttributes.editor.err.inverted',
  no_permitted_integer: 'settings.taskAttributes.editor.err.noInteger',
  unsafe_integer: 'settings.taskAttributes.editor.err.unsafeInteger',
};

/** Server error path → the input it belongs to. */
function fieldIdFor(path: string, type: string): string {
  switch (path) {
    case 'name': return 'ta-name';
    case 'key': return 'ta-name';
    case 'type': return 'ta-type-numeric';
    case 'description': return 'ta-desc';
    case 'constraints.min': return 'ta-min';
    case 'constraints.max': return 'ta-max';
    case 'constraints.integer_only': return 'ta-int';
    case 'constraints.min_length': return 'ta-minlen';
    case 'constraints.max_length': return 'ta-maxlen';
    default: return type === 'string' ? 'ta-minlen' : type === 'numeric' ? 'ta-min' : 'ta-type-numeric';
  }
}

export function DefinitionEditorDialog(props: {
  mode: 'create' | 'edit';
  definitionId?: string;
  onClose: () => void;
  onSaved: (notice: ListNotice) => void;
}): JSX.Element {
  const { mode, definitionId = null, onClose, onSaved } = props;
  const { slug = '' } = useParams<{ slug: string }>();
  const { t, locale } = useTranslation();
  const { label } = useIdentityPresentation();
  const actions = useTaskAttributeActions();
  const read = useDefinition(mode === 'edit' ? definitionId : null);
  const openedAt = useRef(Date.now());

  const [base, setBase] = useState<AttributeDefinitionRead | null>(null);
  const [draft, setDraft] = useState<Draft>(EMPTY);
  const [status, setStatus] = useState<Status>({ s: 'idle' });
  const [staleDraft, setStaleDraft] = useState<DefinitionFields | null>(null);
  const [confirmDiscard, setConfirmDiscard] = useState(false);
  const [copied, setCopied] = useState(false);
  const [observedDefinitions, setObservedDefinitions] = useState<AttributeDefinition[] | null>(null);
  const observedDefinitionsRef = useRef<AttributeDefinition[] | null>(null);
  const sent = useRef<Sent | null>(null);
  const summaryRef = useRef<HTMLDivElement>(null);

  // Edit: the dialog's own read, fetched since it opened, seeds the draft once.
  const freshRead = mode === 'edit' && read.isSuccess && !read.isFetching && read.dataUpdatedAt >= openedAt.current ? read.data ?? null : null;
  useEffect(() => {
    if (freshRead && !base) {
      setBase(freshRead);
      setDraft(draftOf(freshRead.definition));
    }
  }, [freshRead, base]);

  // The error summary takes focus each time a new server error map arrives.
  const validationErrors = status.s === 'validation' ? status.errors : null;
  useEffect(() => {
    if (validationErrors) summaryRef.current?.focus();
  }, [validationErrors]);

  const baseDef = base?.definition ?? null;
  const baseDraft = mode === 'create' ? EMPTY : baseDef ? draftOf(baseDef) : EMPTY;
  const dirty = !sameJson(draft, baseDraft);
  const draftKey = JSON.stringify(draft);

  // Route navigation away with unsaved edits asks through the shared owner gate.
  const owner = useConfigOwner({
    page: 'settings.taskAttributes',
    subject: mode === 'edit' ? baseDef?.key ?? null : null,
    org: slug || null,
    dirty,
    draft,
    onDiscard: onClose,
    onAcknowledgeUnknown: mode === 'create' ? () => { void actions.refreshList(); onClose(); } : undefined,
    // The registry publishes observation readiness synchronously. Its panel
    // must read these rows before React commits this editor's state update.
    unknownObservation: mode === 'create' ? () => observedDefinitionsRef.current
      ? <DefinitionListObservation definitions={observedDefinitionsRef.current} /> : null : undefined,
    checkResult: () => reconcileRef.current(),
  });
  const { requestOwnerTransition } = useOwnerGate();

  // Editability: a refusal's `detail.current` wins; otherwise this dialog's read.
  const shownRead: AttributeDefinitionRead | null =
    status.s === 'archived' || status.s === 'conflict' || status.s === 'frozen' || status.s === 'echoMismatch' ? status.current : base;
  // Field rendering keeps the draft visible after a conflict/frozen refusal
  // (until Reload latest); only an archive refusal locks everything, from
  // `detail.current.editability`.
  const editability: AttributeDefinitionEditability | null =
    mode === 'create' ? null : status.s === 'archived' ? status.current.editability : base?.editability ?? null;
  // `archived` is data (badge, note); only the server's editability or a refusal gates writes.
  const archivedRefusal = status.s === 'archived';
  const shownArchived = mode === 'edit' && !!shownRead?.definition.archived;
  const writeBlocked = status.s === 'permRevoked' || archivedRefusal;
  const canMeta = mode === 'create' || (!!editability?.can_edit_metadata && !archivedRefusal);
  const canStruct = mode === 'create' || (!!editability?.can_edit_structure && !archivedRefusal);
  const needsReload = status.s === 'conflict' || status.s === 'frozen' || status.s === 'echoMismatch';
  const inputsReadOnly = status.s === 'saving' || status.s === 'unknown' || writeBlocked || needsReload;

  const editReady = mode === 'create' || (!!base && !!freshRead);
  const retryAvailable = (status.s === 'failed' || status.s === 'notSaved') && sent.current?.draftKey === draftKey;
  const keyBlocked = status.s === 'keyReserved' && draft.key === status.owner.key;
  const saveDisabled =
    !editReady || status.s === 'saving' || status.s === 'unknown' || needsReload || writeBlocked || keyBlocked
    || status.s === 'refused' || (mode === 'edit' && !dirty && !retryAvailable) || (mode === 'edit' && !canMeta && !canStruct);

  const buildBody = (): Record<string, unknown> => {
    const fields = fieldsFromDraft(draft, baseDef);
    if (mode === 'create') {
      const body: Record<string, unknown> = { type: draft.type, name: draft.name };
      if (fields.description !== null) body.description = fields.description;
      if (Object.keys(fields.constraints).length > 0) body.constraints = fields.constraints;
      return body;
    }
    const b = baseDef as AttributeDefinition;
    const body: Record<string, unknown> = { expected_version: b.version };
    if (canMeta && draft.name !== b.name) body.name = draft.name;
    if (canMeta && fields.description !== b.description) body.description = fields.description;
    if (canStruct) {
      if (draft.type !== b.type) body.type = draft.type;
      if (!sameJson(sortKeys(fields.constraints), sortKeys(b.constraints))) body.constraints = fields.constraints;
    }
    return body;
  };

  const send = async (body: Record<string, unknown>): Promise<void> => {
    // MEM-300: recheck the current read at the moment of sending.
    if (mode === 'edit' && definitionId && !actions.isDefinitionCurrent(definitionId)) return;
    // A refused reentrant submit (or an unreconciled unknown) leaves the
    // original submission record intact.
    if (owner.isBusy() || owner.saveRefused) return;
    const submitted: Sent = { body, draftKey, pre: baseDef };
    setStatus({ s: 'saving' });
    setCopied(false);
    // F1: the write is ticketed and admitted through the owner registry, so a
    // pending/unknown save holds close, navigation and native lifecycle.
    const result = await owner.track<unknown>(
      async () => {
        sent.current = submitted;
        if (mode === 'create') {
          const created = await actions.create(body as AttributeDefinitionCreateFields);
          // An unreadable success body cannot settle this ticket as Saved.
          const definition = created?.definition;
          if (!definition || typeof definition.key !== 'string' || !/^[a-z][a-z0-9_]{0,63}$/.test(definition.key)
              || typeof definition.definition_id !== 'string' || !definition.definition_id || definition.version !== 1) {
            throw new Error('unreadable definition create response');
          }
          return created;
        }
        return actions.update(definitionId as string, body as Partial<AttributeDefinitionFields> & { expected_version: number });
      },
      (error) => (classifyWriteError(error, mode).kind === 'unknown' ? 'unknown' : 'failed'),
    );
    if (!result.ok) {
      if (result.refused) return setStatus(owner.saveRefused ? { s: 'unknown', checking: false, refreshFailed: false } : { s: 'idle' });
      // Desktop admission refused: nothing was sent, the draft stays.
      if ((result.error as Error | null)?.name === 'OwnersReceiptError') return setStatus({ s: 'failed' });
      return handleFailure(result.error);
    }
    if (mode === 'create') {
      void actions.refreshList();
      if (!result.known) return; // acknowledged/forgotten: no late saved claim
      onSaved({ kind: 'created', v: (result.value as AttributeDefinitionRead).definition.version });
      return;
    }
    const saved = result.value as AttributeDefinitionRead;
    void actions.refreshList();
    const { expected_version: _expected, ...fields } = body;
    if (submitted.pre && echoReflects(saved.definition, submitted.pre, fields as Partial<DefinitionFields>)) {
      onSaved({ kind: 'updated', v: saved.definition.version });
    } else {
      setStaleDraft(fieldsFromDraft(draft, baseDef));
      setStatus({ s: 'echoMismatch', current: saved });
    }
  };

  const handleFailure = (error: unknown): void => {
    const failure = classifyWriteError(error, mode);
    if (failure.kind === 'unknown') {
      observedDefinitionsRef.current = null;
      setObservedDefinitions(null);
      setStatus({ s: 'unknown', checking: false, refreshFailed: false });
      return;
    }
    const current = currentDefinitionRead(failure.detail);
    const code = failure.detail?.code ?? failure.code;
    if (failure.status === 422 && failure.detail?.errors) {
      setStatus({ s: 'validation', errors: failure.detail.errors });
      return;
    }
    if (failure.status === 403 && code === 'human_only') return setStatus({ s: 'permRevoked' });
    if (failure.status === 409 && code === 'key_reserved' && current) {
      return setStatus({ s: 'keyReserved', owner: current.definition, reconciled: false });
    }
    if (failure.status === 409 && current && mode === 'edit') {
      void actions.refreshList();
      setStaleDraft(fieldsFromDraft(draft, baseDef));
      if (code === 'definition_archived' || current.definition.archived) return setStatus({ s: 'archived', current });
      if (code === 'definition_structure_frozen') return setStatus({ s: 'frozen', current });
      return setStatus({ s: 'conflict', current });
    }
    if (failure.status >= 500) return setStatus({ s: 'failed' });
    setStatus({ s: 'refused' });
  };

  const save = (): void => {
    if (saveDisabled) return;
    const body = retryAvailable && sent.current ? sent.current.body : buildBody();
    const unsafe = unsafeBoundErrors(draft, baseDraft, body);
    if (Object.keys(unsafe).length > 0) {
      setStatus({ s: 'validation', errors: unsafe });
      return;
    }
    void send(body);
  };

  // Same-context Check result: stable-ID edits reconcile the full snapshot;
  // keyless creates only observe the list (dialog and shared hold gate).
  const reconcileUnknown = async (): Promise<'saved' | 'not_saved' | 'unknown' | 'observed'> => {
    const submitted = sent.current;
    if (!submitted) return 'unknown';
    setStatus({ s: 'unknown', checking: true, refreshFailed: false });
    try {
      if (mode === 'create') {
        const list = await actions.readList();
        // The list is for the user's inspection, never attribution. Even
        // identical rows or an empty list leave this request unknown.
        observedDefinitionsRef.current = list.definitions;
        setObservedDefinitions(list.definitions);
        setStatus({ s: 'unknown', checking: false, refreshFailed: false });
        return 'observed';
      }
      const latest = await actions.readDefinition(definitionId as string);
      const pre = submitted.pre as AttributeDefinition;
      const outcome = reconcileEdit(latest.definition, pre, submitted.body as Partial<DefinitionFields> & { expected_version: number });
      if (outcome.outcome === 'committed') {
        void actions.refreshList();
        onSaved({ kind: 'updated', v: latest.definition.version });
        return 'saved';
      }
      if (outcome.outcome === 'notSaved') {
        setStatus({ s: 'notSaved' });
        return 'not_saved';
      }
      void actions.refreshList();
      setStaleDraft(fieldsFromDraft(draft, baseDef));
      setStatus(latest.definition.archived ? { s: 'archived', current: latest } : { s: 'conflict', current: latest });
      return 'not_saved';
    } catch {
      setStatus({ s: 'unknown', checking: false, refreshFailed: true });
      return 'unknown';
    }
  };
  const reconcileRef = useRef(reconcileUnknown);
  reconcileRef.current = reconcileUnknown;

  const refreshToCheck = async (): Promise<void> => {
    if (status.s !== 'unknown' || !sent.current) return;
    await owner.check().catch(() => undefined);
  };

  const reloadLatest = async (): Promise<void> => {
    if (!definitionId) return;
    try {
      const latest = await actions.readDefinition(definitionId);
      setStaleDraft((prev) => prev ?? fieldsFromDraft(draft, baseDef));
      setBase(latest);
      setDraft(draftOf(latest.definition));
      openedAt.current = 0;
      setStatus({ s: 'idle' });
    } catch {
      // The previous refusal stays shown; nothing was sent.
    }
  };

  const copyDraft = (): void => {
    const { key: _key, ...createDraft } = fieldsFromDraft(draft, baseDef);
    void navigator.clipboard?.writeText(draftClipboardText(mode === 'create' ? createDraft : fieldsFromDraft(draft, baseDef))).then(() => setCopied(true), () => setCopied(false));
  };

  // Every dismissal asks the shared owner gate first: a sent save that is
  // pending or unknown holds (no Discard) until it is reconciled.
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

  const set = <K extends keyof Draft>(k: K, v: Draft[K]): void => setDraft((d) => ({ ...d, [k]: v }));
  const errors = status.s === 'validation' ? status.errors : {};
  const errorFor = (id: string): string[] => Object.entries(errors)
    .filter(([path]) => fieldIdFor(path, draft.type) === id)
    .flatMap(([, codes]) => codes.map((c) => t(ERROR_KEY[c] ?? 'settings.taskAttributes.editor.err.other')));
  const errorCount = new Set(Object.keys(errors).map((p) => fieldIdFor(p, draft.type))).size;

  const loading = mode === 'edit' && !base;
  const loadFailed = mode === 'edit' && !base && read.isError && !read.isFetching;
  const title = mode === 'create' ? t('settings.taskAttributes.editor.createTitle') : t('settings.taskAttributes.editor.editTitle');
  const sub = mode === 'create'
    ? t('settings.taskAttributes.list.org', { org: slug })
    : baseDef ? t('settings.taskAttributes.editor.editSub', { key: baseDef.key, v: baseDef.version }) : '';

  return (
    <Dialog open onOpenChange={(next) => { if (!next) requestClose(); }}>
      <DialogContent
        className="flex max-w-xl min-w-0 flex-col gap-0 overflow-hidden p-0"
        closeLabel={t('settings.taskAttributes.editor.cancel')}
        aria-describedby="ta-editor-sub"
      >
        <div className="px-6 pt-6 pb-3 pr-12">
          <DialogTitle className="font-display text-lg">{title}</DialogTitle>
          <DialogDescription id="ta-editor-sub" className="mt-1 flex flex-wrap items-center gap-2 font-mono text-xs">
            {sub}
            {shownArchived && <StateBadge archived />}
          </DialogDescription>
        </div>
        <div className="min-h-0 flex-1 overflow-y-auto px-6 pt-3 pb-4" data-testid="ta-editor-body">
          {loading ? (
            loadFailed ? (
              <Callout
                tone="danger"
                role="alert"
                actions={<Button variant="outline" className="bg-surface-raised" size="sm" onClick={() => void read.refetch()}>{t('settings.taskAttributes.list.retry')}</Button>}
              >
                {t('settings.taskAttributes.editor.loadError')}
              </Callout>
            ) : (
              <div aria-busy="true" className="flex flex-col gap-3">
                <p role="status" className="text-text-secondary text-sm">{t('settings.taskAttributes.editor.loading')}</p>
                {[0, 1, 2].map((i) => <div key={i} aria-hidden="true" className="bg-surface-raised h-10 rounded-md" />)}
              </div>
            )
          ) : (
            <form
              noValidate
              className="flex min-w-0 flex-col gap-4"
              onSubmit={(e) => { e.preventDefault(); save(); }}
            >
              <StatusNotices
                status={status}
                slug={slug}
                actorLabel={label('founder')}
                locale={locale}
                errorCount={errorCount}
                errors={errors}
                draftType={draft.type}
                summaryRef={summaryRef}
                onRefresh={() => void refreshToCheck()}
                keylessCreate={mode === 'create'}
                definitions={observedDefinitions}
                canAcknowledge={owner.record?.canAcknowledgeUnknown === true}
                onAcknowledge={() => owner.acknowledgeUnknown()}
              />
              {staleDraft && shownRead && (needsReload || status.s === 'archived' || status.s === 'idle' || status.s === 'notSaved') && (
                <Compare draft={staleDraft} latest={shownRead.definition} />
              )}
              <Field id="ta-name" label={t('settings.taskAttributes.editor.name')} help={t('settings.taskAttributes.editor.nameHelp')} errors={errorFor('ta-name')}>
                {(describedBy, invalid) => (
                  <Input id="ta-name" value={draft.name} readOnly={inputsReadOnly || !canMeta} aria-readonly={inputsReadOnly || !canMeta || undefined}
                    aria-invalid={invalid || undefined} aria-describedby={describedBy} maxLength={200}
                    onChange={(e) => set('name', e.target.value)} />
                )}
              </Field>
              {canStruct ? (
                <>
                  {mode === 'edit' && baseDef && <LockedRow id="ta-locked-key" label={t('settings.taskAttributes.editor.key')} value={(shownRead?.definition ?? baseDef).key} mono />}
                  <p className="text-text-secondary text-xs">{t('settings.taskAttributes.editor.keyHelp')}</p>
                  <TypePicker value={draft.type} readOnly={inputsReadOnly} errors={errorFor('ta-type-numeric')} onChange={(v) => set('type', v)} />
                  <ConstraintFields draft={draft} readOnly={inputsReadOnly} set={set} errorFor={errorFor} />
                </>
              ) : (
                <LockedStructure definition={shownRead?.definition ?? baseDef} archived={shownArchived} />
              )}
              <Field
                id="ta-desc"
                label={t('settings.taskAttributes.editor.desc')}
                optional
                help={<>{t('settings.taskAttributes.editor.descHelp')} <span className="font-mono">{t('settings.taskAttributes.editor.descCount', { n: charLength(draft.description) })}</span></>}
                errors={errorFor('ta-desc')}
              >
                {(describedBy, invalid) => (
                  <Textarea id="ta-desc" rows={5} value={draft.description} readOnly={inputsReadOnly || !canMeta} aria-readonly={inputsReadOnly || !canMeta || undefined}
                    aria-invalid={invalid || undefined} aria-describedby={describedBy}
                    onChange={(e) => set('description', e.target.value)} />
                )}
              </Field>
              {mode === 'edit' && canStruct && <Callout tone="info">{t('settings.taskAttributes.editor.unusedNote')}</Callout>}
              {shownArchived && !archivedRefusal && <Callout tone="info">{t('settings.taskAttributes.detail.archivedNote')}</Callout>}
              <p className="text-text-secondary text-sm">{t('settings.taskAttributes.editor.noDefaults')}</p>
              <button type="submit" hidden tabIndex={-1} aria-hidden="true" />
            </form>
          )}
        </div>
        <div className="border-border-default flex flex-wrap items-center justify-end gap-2 border-t px-6 py-3">
          {copied && <span role="status" className="text-text-secondary mr-auto text-xs">{t('settings.taskAttributes.editor.copied')}</span>}
          {(needsReload || status.s === 'archived' || status.s === 'permRevoked' || status.s === 'keyReserved' || status.s === 'refused') && (
            <Button variant="outline" className="bg-surface-raised" onClick={copyDraft}>{t('settings.taskAttributes.editor.copyDraft')}</Button>
          )}
          {mode === 'edit' && (needsReload || status.s === 'archived') && (
            <Button variant="outline" className="bg-surface-raised" onClick={() => void reloadLatest()}>{t('settings.taskAttributes.editor.reload')}</Button>
          )}
          <Button variant="ghost" onClick={requestClose}>{t('settings.taskAttributes.editor.cancel')}</Button>
          {!archivedRefusal && (
            <Button onClick={save} disabled={saveDisabled} loading={status.s === 'saving'}>
              {status.s === 'saving'
                ? t('settings.taskAttributes.editor.saving')
                : retryAvailable
                  ? t('settings.taskAttributes.editor.retry')
                  : mode === 'create' ? t('settings.taskAttributes.editor.saveCreate') : t('settings.taskAttributes.editor.save')}
            </Button>
          )}
        </div>
      </DialogContent>
      <Dialog open={confirmDiscard} onOpenChange={setConfirmDiscard}>
        <DialogContent className="max-w-sm" closeLabel={t('settings.taskAttributes.editor.keepEditing')} onOpenAutoFocus={(e) => {
          e.preventDefault();
          (document.getElementById('ta-keep-editing') as HTMLButtonElement | null)?.focus();
        }}>
          <DialogTitle className="font-display text-lg">{t('settings.taskAttributes.editor.discardTitle')}</DialogTitle>
          <DialogDescription>{t('settings.taskAttributes.editor.discardBody')}</DialogDescription>
          <div className="flex flex-wrap justify-end gap-2">
            <Button id="ta-keep-editing" variant="outline" className="bg-surface-raised" onClick={() => setConfirmDiscard(false)}>{t('settings.taskAttributes.editor.keepEditing')}</Button>
            <Button variant="destructive" onClick={discardAndClose}>{t('settings.taskAttributes.editor.discard')}</Button>
          </div>
        </DialogContent>
      </Dialog>
    </Dialog>
  );
}

function sortKeys(o: Record<string, number | boolean>): Record<string, number | boolean> {
  return Object.fromEntries(Object.entries(o).sort(([a], [b]) => a.localeCompare(b)));
}

function Field({
  id,
  label,
  help,
  optional = false,
  errors,
  children,
}: {
  id: string;
  label: string;
  help?: ReactNode;
  optional?: boolean;
  errors: string[];
  children: (describedBy: string, invalid: boolean) => ReactNode;
}): JSX.Element {
  const { t } = useTranslation();
  const describedBy = [help ? `${id}-help` : null, errors.length ? `${id}-error` : null].filter(Boolean).join(' ');
  return (
    <div className="*:aria-invalid:border-feedback-danger flex min-w-0 flex-col gap-1">
      <label htmlFor={id} className="text-text-primary text-sm font-semibold">
        {label}
        {optional && <span className="text-text-secondary font-normal"> ({t('settings.taskAttributes.editor.optional')})</span>}
      </label>
      {children(describedBy, errors.length > 0)}
      {help && <p id={`${id}-help`} className="text-text-secondary text-xs break-words">{help}</p>}
      {errors.length > 0 && (
        <p id={`${id}-error`} className="text-feedback-danger flex min-w-0 items-start gap-1.5 text-xs break-words">
          <AlertCircle size={14} aria-hidden="true" className="mt-px shrink-0" />
          <span className="min-w-0">{errors.join(' ')}</span>
        </p>
      )}
    </div>
  );
}

function TypePicker({ value, readOnly, errors, onChange }: { value: string; readOnly: boolean; errors: string[]; onChange: (v: string) => void }): JSX.Element {
  const { t } = useTranslation();
  const help: Record<string, MessageKey> = {
    numeric: 'settings.taskAttributes.type.numericHelp',
    string: 'settings.taskAttributes.type.stringHelp',
    boolean: 'settings.taskAttributes.type.booleanHelp',
  };
  return (
    <fieldset className="flex min-w-0 flex-col gap-1" aria-describedby={errors.length ? 'ta-type-error' : undefined}>
      <legend className="text-text-primary mb-1 text-sm font-semibold">{t('settings.taskAttributes.editor.type')}</legend>
      <div className="grid min-w-0 grid-cols-1 gap-2 sm:grid-cols-3">
        {ATTRIBUTE_TYPES.map((type) => {
          const checked = value === type;
          return (
            <label
              key={type}
              className={`flex min-w-0 cursor-pointer items-start gap-2 rounded-md border p-3 text-sm ${
                checked ? 'border-accent-default bg-accent-soft' : 'border-border-default bg-surface-raised'
              } ${readOnly ? 'cursor-default opacity-80' : ''}`}
            >
              <input
                id={`ta-type-${type}`}
                type="radio"
                name="ta-type"
                value={type}
                checked={checked}
                disabled={readOnly && !checked}
                aria-invalid={errors.length > 0 || undefined}
                className="accent-accent-default mt-0.5"
                onChange={() => { if (!readOnly) onChange(type); }}
              />
              <span className="min-w-0">
                <span className="text-text-primary block font-semibold">{t(TYPE_KEY[type] as MessageKey)}</span>
                <span className="text-text-secondary block text-xs">{t(help[type] as MessageKey)}</span>
              </span>
            </label>
          );
        })}
      </div>
      {errors.length > 0 && <p id="ta-type-error" className="text-feedback-danger text-xs">{errors.join(' ')}</p>}
    </fieldset>
  );
}

function ConstraintFields({
  draft,
  readOnly,
  set,
  errorFor,
}: {
  draft: Draft;
  readOnly: boolean;
  set: <K extends keyof Draft>(k: K, v: Draft[K]) => void;
  errorFor: (id: string) => string[];
}): JSX.Element {
  const { t } = useTranslation();
  const numberInput = (id: string, k: 'min' | 'max' | 'minLen' | 'maxLen', labelKey: MessageKey): JSX.Element => (
    <Field id={id} label={t(labelKey)} errors={errorFor(id)}>
      {(describedBy, invalid) => (
        <Input id={id} type="number" inputMode="decimal" value={draft[k]} readOnly={readOnly}
          aria-invalid={invalid || undefined} aria-describedby={describedBy || undefined}
          onChange={(e) => set(k, e.target.value)} />
      )}
    </Field>
  );
  return (
    <section className="flex min-w-0 flex-col gap-2" aria-labelledby="ta-constraints-heading">
      <h3 id="ta-constraints-heading" className="text-overline text-text-secondary tracking-wider uppercase">
        {t('settings.taskAttributes.editor.constraints')}{' '}
        <span className="font-normal normal-case">({t('settings.taskAttributes.editor.optional')})</span>
      </h3>
      {draft.type === '' && <p className="text-text-secondary text-sm">{t('settings.taskAttributes.editor.typeChoose')}</p>}
      {draft.type === 'numeric' && (
        <>
          <div className="grid min-w-0 grid-cols-1 gap-3 sm:grid-cols-2">
            {numberInput('ta-min', 'min', 'settings.taskAttributes.editor.min')}
            {numberInput('ta-max', 'max', 'settings.taskAttributes.editor.max')}
          </div>
          <label className="flex items-center gap-2 text-sm">
            <input id="ta-int" type="checkbox" checked={draft.integerOnly} disabled={readOnly}
              className="accent-accent-default size-4" onChange={(e) => set('integerOnly', e.target.checked)} />
            {t('settings.taskAttributes.editor.int')}
          </label>
          {errorFor('ta-int').length > 0 && <p className="text-feedback-danger text-xs">{errorFor('ta-int').join(' ')}</p>}
        </>
      )}
      {draft.type === 'string' && (
        <>
          <div className="grid min-w-0 grid-cols-1 gap-3 sm:grid-cols-2">
            {numberInput('ta-minlen', 'minLen', 'settings.taskAttributes.editor.minLen')}
            {numberInput('ta-maxlen', 'maxLen', 'settings.taskAttributes.editor.maxLen')}
          </div>
          <p className="text-text-secondary text-xs">{t('settings.taskAttributes.editor.lenHelp')}</p>
        </>
      )}
      {draft.type === 'boolean' && <p className="text-text-secondary text-sm">{t('settings.taskAttributes.editor.boolNone')}</p>}
    </section>
  );
}

function LockedStructure({ definition, archived }: { definition: AttributeDefinition | null; archived: boolean }): JSX.Element | null {
  const { t } = useTranslation();
  if (!definition) return null;
  return (
    <>
      <LockedRow id="ta-locked-key" label={t('settings.taskAttributes.editor.key')} value={definition.key} mono />
      <LockedRow id="ta-locked-type" label={t('settings.taskAttributes.editor.type')} value={typeLabel(t, definition.type)} />
      <LockedRow
        id="ta-locked-constraints"
        overline
        label={<>{t('settings.taskAttributes.editor.constraints')} <span className="font-normal normal-case">({t('settings.taskAttributes.editor.optional')})</span></>}
        value={constraintSummary(t, definition)}
      />
      {!archived && <Callout tone="info">{t('settings.taskAttributes.editor.lockedNote')}</Callout>}
    </>
  );
}

function StatusNotices({
  status,
  slug,
  actorLabel,
  locale,
  errorCount,
  errors,
  draftType,
  summaryRef,
  onRefresh,
  keylessCreate,
  definitions,
  canAcknowledge,
  onAcknowledge,
}: {
  status: Status;
  slug: string;
  actorLabel: string;
  locale: ReturnType<typeof useTranslation>['locale'];
  errorCount: number;
  errors: Record<string, string[]>;
  draftType: string;
  summaryRef: React.RefObject<HTMLDivElement>;
  onRefresh: () => void;
  keylessCreate: boolean;
  definitions: AttributeDefinition[] | null;
  canAcknowledge: boolean;
  onAcknowledge: () => void;
}): JSX.Element | null {
  const { t } = useTranslation();
  switch (status.s) {
    case 'saving':
      return <p role="status" className="sr-only">{t('settings.taskAttributes.editor.saving')}</p>;
    case 'validation':
      return (
        <div ref={summaryRef} tabIndex={-1} role="alert" className="border-status-escalated/30 bg-tier-red-tint text-status-escalated rounded-md border px-3 py-2 text-sm focus-visible:ring-2 focus-visible:outline-none">
          <p className="font-semibold">{t('settings.taskAttributes.editor.errSummary', { count: errorCount })}</p>
          <ul className="mt-1 list-disc pl-5">
            {[...new Set(Object.keys(errors).map((p) => fieldIdFor(p, draftType)))].map((id) => (
              <li key={id}>
                <a href={`#${id}`} className="underline" onClick={(e) => { e.preventDefault(); document.getElementById(id)?.focus(); }}>
                  {Object.entries(errors).filter(([p]) => fieldIdFor(p, draftType) === id)
                    .flatMap(([, codes]) => codes.map((c) => t(ERROR_KEY[c] ?? 'settings.taskAttributes.editor.err.other'))).join(' ') || fieldLabel(t, id)}
                </a>
              </li>
            ))}
          </ul>
        </div>
      );
    case 'failed':
      return <Callout tone="danger" role="alert">{t('settings.taskAttributes.editor.saveFailed')}</Callout>;
    case 'unknown':
      return (
        <Callout
          tone="attention"
          role="alert"
          title={t('settings.taskAttributes.editor.unknownTitle')}
          actions={<div className="flex flex-wrap gap-2">
            <Button type={keylessCreate ? 'button' : undefined} variant="outline" className="bg-surface-raised" size="sm" loading={status.checking} disabled={status.checking} onClick={onRefresh}>{t(keylessCreate ? 'owners.save.checkList' : 'settings.taskAttributes.editor.refresh')}</Button>
            {keylessCreate && <Button type="button" variant="outline" className="bg-surface-raised" size="sm" disabled={!canAcknowledge || status.checking} onClick={onAcknowledge}>{t('owners.save.acknowledgeUnknown')}</Button>}
          </div>}
        >
          {t(keylessCreate ? 'owners.save.unknownCreate' : 'settings.taskAttributes.editor.unknownBody')}
          {keylessCreate && definitions && <DefinitionListObservation definitions={definitions} />}
          {status.refreshFailed && <> {t('settings.taskAttributes.editor.refreshFailed')}</>}
        </Callout>
      );
    case 'notSaved':
      return <Callout tone="attention" role="alert">{t('settings.taskAttributes.editor.notSaved')}</Callout>;
    case 'keyReserved':
      return (
        <Callout tone="danger" role="alert">
          {t('settings.taskAttributes.editor.keyReserved', {
            key: status.owner.key,
            name: status.owner.name,
            state: t(status.owner.archived ? 'settings.taskAttributes.state.archived' : 'settings.taskAttributes.state.active'),
          })}
          {status.reconciled && <> {t('settings.taskAttributes.editor.keyReservedNotSaved')}</>}
        </Callout>
      );
    case 'conflict':
      return (
        <Callout tone="attention" role="alert" title={t('settings.taskAttributes.editor.conflictTitle')}>
          {t('settings.taskAttributes.editor.conflictBody', {
            v: status.current.definition.version,
            actor: actorLabel,
            time: formatWhen(locale, status.current.definition.recorded_at),
          })}
        </Callout>
      );
    case 'frozen':
      return <Callout tone="attention" role="alert">{t('settings.taskAttributes.editor.frozenBody')}</Callout>;
    case 'archived':
      return (
        <Callout tone="attention" role="alert">
          {t('settings.taskAttributes.editor.archivedRefused')} {t('settings.taskAttributes.detail.archivedNote')}
        </Callout>
      );
    case 'echoMismatch':
      return <Callout tone="attention" role="alert">{t('settings.taskAttributes.editor.echoMismatch')}</Callout>;
    case 'permRevoked':
      return <Callout tone="danger" role="alert">{t('settings.taskAttributes.editor.permRevoked', { org: slug })}</Callout>;
    case 'refused':
      return <Callout tone="danger" role="alert">{t('settings.taskAttributes.editor.refused')}</Callout>;
    default:
      return null;
  }
}

function DefinitionListObservation({ definitions }: { definitions: AttributeDefinition[] }): JSX.Element {
  const { t } = useTranslation();
  return (
    <span role="list" className="mt-2 block max-h-40 min-w-0 max-w-full overflow-y-auto pl-4" tabIndex={0} aria-label={t('settings.taskAttributes.list.title')} data-testid="ta-checked-list">
      {definitions.length === 0 && <span role="listitem" className="block">{t('settings.taskAttributes.list.emptyTitle')}</span>}
      {definitions.map((d) => <span role="listitem" key={d.definition_id} className="block break-words">{d.name} · <span className="font-mono break-all">{d.key}</span></span>)}
    </span>
  );
}

function fieldLabel(t: ReturnType<typeof useTranslation>['t'], id: string): string {
  const keys: Record<string, MessageKey> = {
    'ta-name': 'settings.taskAttributes.editor.name',
    'ta-key': 'settings.taskAttributes.editor.key',
    'ta-type-numeric': 'settings.taskAttributes.editor.type',
    'ta-desc': 'settings.taskAttributes.editor.desc',
    'ta-min': 'settings.taskAttributes.editor.min',
    'ta-max': 'settings.taskAttributes.editor.max',
    'ta-int': 'settings.taskAttributes.editor.int',
    'ta-minlen': 'settings.taskAttributes.editor.minLen',
    'ta-maxlen': 'settings.taskAttributes.editor.maxLen',
  };
  return t(keys[id] ?? 'settings.taskAttributes.editor.constraints');
}

/** "Your draft · not saved" next to "Latest saved · vN", for the fields that differ. */
function Compare({ draft, latest }: { draft: DefinitionFields; latest: AttributeDefinition }): JSX.Element {
  const { t } = useTranslation();
  const latestFields = fieldsOf(latest);
  const rows: { label: string; mine: string; theirs: string; mono?: boolean }[] = [
    { label: t('settings.taskAttributes.editor.name'), mine: draft.name, theirs: latestFields.name },
    { label: t('settings.taskAttributes.editor.key'), mine: draft.key, theirs: latestFields.key, mono: true },
    { label: t('settings.taskAttributes.editor.type'), mine: typeLabel(t, draft.type), theirs: typeLabel(t, latestFields.type) },
    { label: t('settings.taskAttributes.editor.constraints'), mine: constraintSummary(t, { type: draft.type as AttributeDefinition['type'], constraints: draft.constraints }), theirs: constraintSummary(t, latest) },
    { label: t('settings.taskAttributes.editor.desc'), mine: draft.description ?? '', theirs: latestFields.description ?? '' },
  ];
  const shown = rows.filter((r) => r.mine !== r.theirs);
  const list = (side: 'mine' | 'theirs'): JSX.Element => (
    <dl className="flex flex-col gap-1 text-xs">
      {shown.map((r) => (
        <div key={r.label} className="flex min-w-0 flex-wrap gap-x-3">
          <dt className="text-text-secondary shrink-0">{r.label}</dt>
          <dd className={`min-w-0 break-words whitespace-pre-wrap ${r.mono ? 'font-mono' : ''} ${side === 'mine' ? 'bg-attention-soft rounded-sm px-1' : ''}`}>{r[side]}</dd>
        </div>
      ))}
    </dl>
  );
  return (
    <div className="grid min-w-0 grid-cols-1 gap-2 sm:grid-cols-2" data-testid="ta-compare">
      <div className="border-border-default bg-surface-raised min-w-0 rounded-md border p-3">
        <p className="text-overline text-text-secondary mb-2 tracking-wider uppercase">{t('settings.taskAttributes.editor.yourDraft')}</p>
        {list('mine')}
      </div>
      <div className="border-border-default bg-surface-raised min-w-0 rounded-md border p-3">
        <p className="text-overline text-text-secondary mb-2 tracking-wider uppercase">{t('settings.taskAttributes.editor.latest', { v: latest.version })}</p>
        {list('theirs')}
      </div>
    </div>
  );
}
