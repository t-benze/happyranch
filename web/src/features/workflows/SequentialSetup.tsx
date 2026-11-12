/**
 * U8b Workflows Setup: edit the CURRENT repo-free default agents of the stored
 * sequential definition (latest version) through the merged U7b routes only.
 *
 * Save custody (PRD F7, AC10) is owned by the U7b provider fence:
 * - one explicit Save freezes one operation key/body; while it is pending every
 *   edit, Save and Discard is locked (render AND handler);
 * - a definite refusal releases the fence; a stale/conflict refusal keeps the
 *   draft and requires explicit "Continue from current configuration";
 * - an uncertain outcome keeps the original key/body and offers ONLY the
 *   read-only original-save lookup; absent/error stay uncertain and locked; a
 *   found receipt is shown apart from the current settings read.
 * Drafts and lookup outcomes live per QueryClient per org (never persisted), so
 * navigation, org A->B->A and locale changes keep them without any request.
 *
 * Saving never changes new-run admission (first save starts Disabled) and this
 * view has no admission, repository, start, retry, reassign or merge control.
 */
import { useEffect, useReducer } from 'react';
import { useQueryClient, type QueryClient } from '@tanstack/react-query';
import { Button } from '@/design-system/primitives/Button';
import { useAgentsList } from '@/hooks/agents';
import { useTranslation } from '@/hooks/i18n';
import {
  useSequentialDefinitions,
  useSequentialSettings,
  useSequentialSettingsActions,
  useSequentialSettingsFence,
} from '@/hooks/workflows';
import { ApiError } from '@/lib/api';
import { parseDefinition, type DefView } from '@/lib/workflowSequentialView';
import { isAccessRefusal } from '@/shared/workflows/SequentialTaskBanner';
import { ReadState, StaleNotice } from './SequentialCommon';
import { reviewDiagnostic } from './strings';

type Actions = ReturnType<typeof useSequentialSettingsActions>;
type Command = Parameters<Actions['save']>[0];
type Receipt = Awaited<ReturnType<Actions['save']>>;
type Settings = Receipt['original'];
type Pin = Command['definition'];
type Limits = Command['limits'];
type Binding = Command['bindings'][number];

interface Draft {
  pin: Pin;
  bindings: Record<string, string>;
  order: string[];
  limits: Limits;
  /** expected_generation for the next save (0 = nothing saved yet). */
  basis: number;
  dirty: boolean;
}
type Outcome =
  | { kind: 'none' }
  | { kind: 'saved'; receipt: Receipt }
  | { kind: 'confirmed'; receipt: Receipt }
  /** `rejected` is the expected_generation the server refused; adoption needs a read past it. */
  | { kind: 'conflict'; code: string; rejected: number }
  | { kind: 'refused'; status: number; code: string };
type Lookup = { kind: 'reading' } | { kind: 'absent' } | { kind: 'error'; detail: string };
/**
 * The newest generation this form knows was committed (its own save receipt or
 * confirmed original lookup). It is a hydration floor only: a current read older
 * than it never replaces the editor, and it is never written into the read cache.
 */
interface Committed { identity: string; settings: Settings }
interface Memory { draft: Draft | null; outcome: Outcome; lookup: { key: string; state: Lookup } | null; committed: Committed | null }

/** Per-QueryClient, per-org setup memory: survives pane unmount and org switches, never persisted. */
const memories = new WeakMap<QueryClient, Map<string, { value: Memory; listeners: Set<() => void> }>>();
function memoryCell(qc: QueryClient, slug: string) {
  let byOrg = memories.get(qc);
  if (!byOrg) { byOrg = new Map(); memories.set(qc, byOrg); }
  let cell = byOrg.get(slug);
  if (!cell) { cell = { value: { draft: null, outcome: { kind: 'none' }, lookup: null, committed: null }, listeners: new Set() }; byOrg.set(slug, cell); }
  return cell;
}
function useSetupMemory(slug: string): [Memory, (change: (m: Memory) => Memory) => void] {
  const cell = memoryCell(useQueryClient(), slug);
  const [, rerender] = useReducer((n: number) => n + 1, 0);
  useEffect(() => { cell.listeners.add(rerender); return () => { cell.listeners.delete(rerender); }; }, [cell]);
  const update = (change: (m: Memory) => Memory) => {
    cell.value = change(cell.value);
    cell.listeners.forEach(listener => listener());
  };
  return [cell.value, update];
}

const CONFLICT = new Set(['sequential_settings_generation_stale', 'sequential_settings_operation_conflict']);
const errCode = (error: unknown): string | null => (error as { code?: string | null } | null)?.code ?? null;
const isNotSaved = (error: unknown): boolean => error instanceof ApiError && error.status === 404 && error.code === 'sequential_settings_not_found';
const shortDigest = (digest: string): string => `${digest.slice(0, 12)}…`;

function draftFrom(def: DefView, pin: Pin, current: Settings | null): Draft {
  const saved = current?.snapshot.bindings ?? [];
  const roles = def.roles.map(r => r.name);
  const savedOrder = saved.map(b => b.role);
  const sameSet = savedOrder.length === roles.length && roles.every(r => savedOrder.includes(r));
  return {
    pin,
    bindings: Object.fromEntries(roles.map(r => [r, saved.find(b => b.role === r)?.principal ?? ''])),
    order: sameSet ? savedOrder : roles,
    limits: current?.snapshot.limits ?? { ...def.limits },
    basis: current?.generation ?? 0,
    dirty: false,
  };
}

export function SequentialSetup({ slug }: { slug: string }): JSX.Element {
  const { t } = useTranslation();
  const defs = useSequentialDefinitions();
  return <ReadState q={defs}>{() => {
    const data = defs.data!;
    if (data.storage !== 'storage readable') return <div className="border-border-default rounded-md border p-4" role="status"><p className="font-medium">{t('seqwf.unavailable')}</p><p className="text-text-muted mt-1 text-sm">{t('seqwf.unavailableBody')}</p></div>;
    const latest = [...data.definitions].sort((a, b) => b.pin.version - a.pin.version)[0];
    if (!latest) return <p className="text-text-muted">{t('seqwf.definitionsEmpty')}</p>;
    const def = parseDefinition(latest.definition_json);
    if (!def) return <p role="alert">{t('seqwf.defUnparsed')}</p>;
    return <SetupForm key={JSON.stringify([slug, latest.id])} slug={slug} def={def} pin={latest.pin} />;
  }}</ReadState>;
}

function SetupForm({ slug, def, pin }: { slug: string; def: DefView; pin: Pin }): JSX.Element {
  const { t } = useTranslation();
  const settings = useSequentialSettings(pin.identity);
  const actions = useSequentialSettingsActions();
  const fence = useSequentialSettingsFence();
  const agents = useAgentsList();
  const [memory, update] = useSetupMemory(slug);

  const notSaved = settings.isError && isNotSaved(settings.error);
  const current: Settings | null = notSaved ? null : settings.data ?? null;
  const currentKnown = notSaved || settings.data !== undefined;

  // Hydrate (or re-hydrate an untouched draft) from the CURRENT read only, and
  // never from a read older than the generation this form knows was committed:
  // the submitted values and basis stay until a read at >= that generation arrives.
  useEffect(() => {
    if (!currentKnown || fence) return;
    update(m => {
      const known = m.committed?.identity === pin.identity ? m.committed.settings : null;
      const floor = known?.generation ?? 0;
      // A read older than the known commit never becomes the source (or CAS basis) of a draft.
      const fresh = draftFrom(def, pin, known && (!current || current.generation < floor) ? known : current);
      if (!m.draft || m.draft.pin.definition_digest !== pin.definition_digest) return { ...m, draft: fresh };
      if (!m.draft.dirty && m.outcome.kind !== 'conflict' && m.draft.basis !== fresh.basis && fresh.basis >= floor) return { ...m, draft: fresh };
      return m;
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps -- keyed by the read's generation
  }, [currentKnown, current?.generation, current?.snapshot_digest, fence, pin.definition_digest]);

  if (settings.isError && !notSaved) {
    if (isAccessRefusal(settings.error)) return <ReadState q={settings}>{() => null}</ReadState>;
  }
  const draft = memory.draft;
  const committed = memory.committed?.identity === pin.identity ? memory.committed.settings : null;
  // The newest known saved configuration: the current read unless it is older than this form's own commit.
  const baseline: Settings | null = committed && (!current || current.generation < committed.generation) ? committed : current;
  const locked = fence !== null;
  const sending = fence?.phase === 'sending';
  const uncertain = fence?.phase === 'uncertain';
  const lookup = fence && memory.lookup?.key === fence.command.operation_key ? memory.lookup.state : null;

  const roleNodes = (role: string) => def.nodes.filter(n => n.role === role);
  const independenceClash = (role: string): string[] => {
    if (!draft) return [];
    const value = draft.bindings[role];
    if (!value) return [];
    const r = def.roles.find(x => x.name === role);
    const others = def.roles.filter(x => x.name !== role && (r?.independentOf.includes(x.name) || x.independentOf.includes(role)));
    return others.filter(o => draft.bindings[o.name] === value).map(o => o.name);
  };
  const incomplete = !draft || def.roles.some(r => !draft.bindings[r.name]);
  const clash = !!draft && def.roles.some(r => independenceClash(r.name).length > 0);
  const conflictPending = memory.outcome.kind === 'conflict';
  // Continue only from an independently read generation that cannot lower the CAS
  // basis: newer than the refused basis for a stale conflict, and never below the
  // known commit. Until then the draft and the conflict lock stay (handler AND control).
  const adoptFloor = memory.outcome.kind !== 'conflict' ? Infinity
    : Math.max(committed?.generation ?? 0, memory.outcome.code === 'sequential_settings_generation_stale' ? memory.outcome.rejected + 1 : memory.outcome.rejected);
  const adoptable = !locked && !!current && current.generation >= adoptFloor;
  // Unchanged configured defaults have nothing to save; a first save needs a complete draft.
  const saveBlocked = locked || !draft || incomplete || clash || conflictPending || !currentKnown || (!!current && !draft.dirty);

  const edit = (role: string, principal: string) => {
    if (fence) return;
    update(m => m.draft ? { ...m, draft: { ...m.draft, bindings: { ...m.draft.bindings, [role]: principal }, dirty: true },
      outcome: m.outcome.kind === 'conflict' ? m.outcome : { kind: 'none' } } : m);
  };
  const discard = () => {
    if (fence || !currentKnown) return;
    // Discarding edits does not resolve a conflict that still waits for a newer read.
    update(m => ({ ...m, draft: draftFrom(def, pin, baseline), outcome: m.outcome.kind === 'conflict' && !adoptable ? m.outcome : { kind: 'none' } }));
  };
  const adopt = () => {
    if (!adoptable || !current) return;
    // Keep edited values on the newer basis; an unedited draft simply takes the newer read.
    update(m => m.draft ? { ...m, outcome: { kind: 'none' },
      draft: m.draft.dirty ? { ...m.draft, basis: current.generation } : draftFrom(def, pin, current) } : m);
  };
  const save = async () => {
    if (saveBlocked || !draft) return;
    const command: Command = {
      format: 'workflow-sequential-settings-put@1',
      operation_key: `sequential-settings:${crypto.randomUUID()}`,
      expected_generation: draft.basis,
      definition: draft.pin,
      bindings: draft.order.map((role): Binding => ({ role, principal: draft.bindings[role] })),
      limits: draft.limits,
    };
    update(m => ({ ...m, outcome: { kind: 'none' }, lookup: null }));
    const rejected = command.expected_generation;
    // The provider takes the org fence synchronously before its first await; the
    // continuation writes to provider-lifetime memory, so it lands even after unmount.
    try {
      const receipt = await actions.save(command);
      update(m => withCommit(m, pin.identity, { kind: 'saved', receipt }));
    } catch (error) {
      // The provider refused a second send synchronously: nothing was sent.
      if (error instanceof Error && error.message === 'sequential_settings_send_in_progress') return;
      const code = errCode(error) ?? '';
      if (CONFLICT.has(code)) {
        update(m => ({ ...m, outcome: { kind: 'conflict', code, rejected } }));
        void settings.refetch();
      } else if (error instanceof ApiError && actionsReleased(code)) {
        update(m => ({ ...m, outcome: { kind: 'refused', status: error.status, code } }));
      }
      // Anything else: the provider kept the fence as `uncertain`.
    }
  };
  const check = async () => {
    if (!fence || fence.phase !== 'uncertain' || lookup?.kind === 'reading') return;
    const key = fence.command.operation_key;
    update(m => ({ ...m, lookup: { key, state: { kind: 'reading' } } }));
    try {
      const receipt = await actions.readOperation(key);
      update(m => ({ ...withCommit(m, pin.identity, { kind: 'confirmed', receipt }), lookup: null }));
    } catch (error) {
      const absent = error instanceof ApiError && error.status === 404 && error.code === 'sequential_settings_operation_not_found';
      update(m => ({ ...m, lookup: { key, state: absent ? { kind: 'absent' } : { kind: 'error', detail: reviewDiagnostic(error) } } }));
    }
  };

  const agentNames = (agents.data?.agents ?? []).map(a => a.name).sort();
  const choicesFor = (role: string): string[] => {
    const set = new Set(agentNames);
    const saved = current?.snapshot.bindings.find(b => b.role === role)?.principal;
    if (saved) set.add(saved);
    if (draft?.bindings[role]) set.add(draft.bindings[role]);
    return [...set].sort();
  };
  const agentTeam = (name: string) => agents.data?.agents.find(a => a.name === name)?.team ?? null;

  return <div className="flex flex-col gap-4">
    {settings.refreshFailed && <StaleNotice error={settings.refreshError} refetch={settings.refetch} />}

    {/* Effective saved configuration (current read), or Last confirmed while uncertain. */}
    <section aria-labelledby="seqwf-setup-current" className="border-border-default bg-bg-raised rounded-lg border">
      <header className="border-border-default flex flex-wrap items-baseline gap-x-3 gap-y-1 border-b px-4 py-3">
        <h2 id="seqwf-setup-current" className="text-text-primary font-medium">{t(uncertain ? 'seqwf.setup.lastConfirmed' : 'seqwf.setup.current')}</h2>
        <span className="text-text-muted text-xs">{t('seqwf.versionLink', { identity: pin.identity, version: pin.version })}</span>
      </header>
      {settings.isError && !notSaved ? <div role="alert" className="p-4"><p className="font-medium">{t('seqwf.setup.currentError')}</p>
        <pre className="text-text-muted mt-2 text-xs whitespace-pre-wrap wrap-anywhere">{reviewDiagnostic(settings.error)}</pre>
        <Button className="mt-3" size="sm" variant="outline" onClick={() => void settings.refetch()}>{t('workflows.refresh')}</Button></div>
        : !currentKnown ? <p role="status" className="text-text-muted p-4">{t('seqwf.loading')}</p>
          : !current ? <p className="p-4 text-sm">{t('seqwf.setup.notConfigured')}</p>
            : <div className="grid grid-cols-1 md:grid-cols-2">
              <div className="min-w-0 p-4"><p className="text-text-muted text-xs uppercase">{t('seqwf.setup.savedDefaults')}</p>
                <BindingList bindings={current.snapshot.bindings} /></div>
              <div className="border-border-default min-w-0 border-t p-4 md:border-t-0 md:border-l">
                <p className="text-text-muted text-xs uppercase">{t('seqwf.setup.newRuns')}</p>
                <p className="mt-1"><span className={`rounded-md px-2 py-0.5 text-sm font-medium ${current.snapshot.admission === 'Allowed' ? 'bg-tier-green-tint text-tier-green' : 'bg-bg-muted text-text-primary'}`}>{t(current.snapshot.admission === 'Allowed' ? 'seqwf.setup.admissionAllowed' : 'seqwf.setup.admissionDisabled')}</span></p>
                <p className="text-text-muted mt-2 text-xs wrap-anywhere">{t('seqwf.setup.generation', { generation: current.generation, digest: shortDigest(current.snapshot_digest) })}</p>
                <p className="text-text-muted mt-1 text-xs">{t('seqwf.limits', { returns: current.snapshot.limits.content_returns, epochs: current.snapshot.limits.max_content_epochs, visits: current.snapshot.limits.max_visits, events: current.snapshot.limits.max_events })}</p>
              </div>
            </div>}
    </section>

    {sending && fence && <div role="status" className="bg-info-soft rounded-lg px-4 py-3 text-sm">
      <p className="font-medium">{t('seqwf.setup.sending')}</p>
      <p className="text-text-muted mt-1 font-mono text-xs break-all">{fence.command.operation_key}</p>
    </div>}

    {uncertain && fence && <section role="alert" aria-labelledby="seqwf-setup-uncertain" className="border-attention bg-attention-soft rounded-lg border px-4 py-3 text-sm">
      <h2 id="seqwf-setup-uncertain" className="text-text-primary font-medium">{t('seqwf.setup.uncertain')}</h2>
      <p className="mt-1">{t('seqwf.setup.uncertainBody')}</p>
      <div className="mt-3 grid grid-cols-1 gap-3 md:grid-cols-2">
        <div className="min-w-0"><p className="text-text-muted text-xs uppercase">{t('seqwf.setup.originalSave')}</p>
          <BindingList bindings={fence.command.bindings} />
          <p className="text-text-muted mt-1 text-xs wrap-anywhere">{t('seqwf.setup.expectedGeneration', { generation: fence.command.expected_generation })}</p></div>
        <div className="min-w-0"><p className="text-text-muted text-xs uppercase">{current ? t('seqwf.setup.lastConfirmedGeneration', { generation: current.generation }) : t('seqwf.setup.lastConfirmedNone')}</p>
          {current && <BindingList bindings={current.snapshot.bindings} />}</div>
      </div>
      <p className="text-text-muted mt-2 font-mono text-xs break-all">{fence.command.operation_key}</p>
      {lookup?.kind === 'absent' && <p className="mt-2">{t('seqwf.setup.lookupAbsent')}</p>}
      {lookup?.kind === 'error' && <p className="mt-2">{t('seqwf.setup.lookupError')} <span className="text-text-muted font-mono text-xs wrap-anywhere">{lookup.detail}</span></p>}
      <div className="mt-3 flex flex-wrap items-center gap-2">
        <Button size="sm" variant="outline" onClick={() => void check()} aria-disabled={lookup?.kind === 'reading'} className="aria-disabled:opacity-50">
          {t(lookup?.kind === 'reading' ? 'seqwf.setup.checking' : 'seqwf.setup.check')}</Button>
        <span className="text-text-muted text-xs">{t('seqwf.setup.checkHelp')}</span>
      </div>
    </section>}

    {!fence && (memory.outcome.kind === 'saved' || memory.outcome.kind === 'confirmed') && <section role="status" aria-labelledby="seqwf-setup-receipt" className="border-tier-green bg-tier-green-tint rounded-lg border px-4 py-3 text-sm">
      <h2 id="seqwf-setup-receipt" className="text-text-primary font-medium">{t(memory.outcome.kind === 'saved' ? 'seqwf.setup.saved' : 'seqwf.setup.confirmed', { generation: memory.outcome.receipt.original.generation })}</h2>
      <p className="mt-1">{t('seqwf.setup.noRunCreated')}</p>
      <div className="mt-3 grid grid-cols-1 gap-3 md:grid-cols-2">
        <div className="min-w-0"><p className="text-text-muted text-xs uppercase">{t('seqwf.setup.receiptHeading', { generation: memory.outcome.receipt.original.generation })}</p>
          <BindingList bindings={memory.outcome.receipt.original.snapshot.bindings} />
          <p className="text-text-muted mt-1 text-xs">{t(memory.outcome.receipt.original.snapshot.admission === 'Allowed' ? 'seqwf.setup.admissionAllowed' : 'seqwf.setup.admissionDisabled')}</p></div>
        <div className="min-w-0"><p className="text-text-muted text-xs uppercase">{current ? t('seqwf.setup.currentGeneration', { generation: current.generation }) : t('seqwf.loading')}</p>
          {current && current.generation > memory.outcome.receipt.original.generation
            ? <><BindingList bindings={current.snapshot.bindings} /><p className="mt-1 text-xs">{t('seqwf.setup.currentNewer')}</p></>
            : current && current.generation < memory.outcome.receipt.original.generation
              ? <p className="text-xs">{t('seqwf.setup.currentOlder')}</p>
              : current && <p className="text-xs">{t('seqwf.setup.currentSame')}</p>}</div>
      </div>
      <p className="text-text-muted mt-2 font-mono text-xs break-all">{memory.outcome.receipt.operation_key}</p>
    </section>}

    {!fence && memory.outcome.kind === 'conflict' && <section role="alert" className="border-attention bg-attention-soft rounded-lg border px-4 py-3 text-sm">
      <h2 className="text-text-primary font-medium">{t('seqwf.setup.conflict')}</h2>
      {adoptable && current
        ? <p className="mt-1">{t('seqwf.setup.conflictBody', { generation: current.generation })}</p>
        : <p className="mt-1">{t('seqwf.setup.conflictWaiting', { generation: adoptFloor - 1 })}</p>}
      <p className="text-text-muted mt-1 font-mono text-xs">{memory.outcome.code}</p>
      <div className="mt-3 flex flex-wrap items-center gap-2">
        <Button size="sm" variant="outline" onClick={adopt} aria-disabled={!adoptable} className="aria-disabled:opacity-50">{t('seqwf.setup.adopt')}</Button>
        {!adoptable && <Button size="sm" variant="outline" onClick={() => void settings.refetch()}>{t('workflows.refresh')}</Button>}
        <span className="text-text-muted text-xs">{adoptable ? t('seqwf.setup.adoptHelp') : t('seqwf.setup.adoptWaitingHelp', { generation: adoptFloor - 1 })}</span>
      </div>
    </section>}

    {!fence && memory.outcome.kind === 'refused' && <div role="alert" className="border-danger bg-danger-soft rounded-lg border px-4 py-3 text-sm">
      <p className="text-text-primary font-medium">{t('seqwf.setup.refused', { status: memory.outcome.status })}</p>
      <p className="text-text-muted mt-1 font-mono text-xs">{memory.outcome.code}</p>
    </div>}

    {/* Default agents: one row per definition role. */}
    {draft && <section aria-labelledby="seqwf-setup-agents" className="border-border-default bg-bg-raised rounded-lg border">
      <header className="border-border-default flex flex-wrap items-baseline gap-x-3 gap-y-1 border-b px-4 py-3">
        <h2 id="seqwf-setup-agents" className="text-text-primary font-medium">{t('seqwf.setup.defaults')}</h2>
        <span className="text-text-muted text-xs">{t('seqwf.setup.defaultsHint', { count: def.roles.length })}</span>
        {draft.dirty && !locked && <span className="bg-info-soft rounded-md px-2 py-0.5 text-xs">{t('seqwf.setup.unsaved')}</span>}
        {locked && <span className="bg-attention-soft rounded-md px-2 py-0.5 text-xs">{t('seqwf.setup.locked')}</span>}
      </header>
      <div className="border-border-default border-b px-4 py-3 text-sm">
        <p>{t('seqwf.setup.defaultsBody')}</p>
        <p className="text-text-muted mt-1">{t('seqwf.setup.eligibilityBody')}</p>
        {agents.isError && <p className="text-text-muted mt-1 text-xs">{t('seqwf.setup.agentsUnavailable')}</p>}
      </div>
      <ul>{def.roles.map(role => {
        const id = `seqwf-role-${role.name}`;
        const value = (locked ? fence.command.bindings.find(b => b.role === role.name)?.principal : draft.bindings[role.name]) ?? '';
        const savedValue = baseline?.snapshot.bindings.find(b => b.role === role.name)?.principal ?? '';
        const clashes = locked ? [] : independenceClash(role.name);
        return <li key={role.name} className="border-border-default grid grid-cols-1 gap-3 border-b px-4 py-3 text-sm last:border-b-0 md:grid-cols-3">
          <div className="min-w-0 wrap-anywhere md:col-span-2">
            <h3 className="font-medium">{role.name}{!locked && value !== savedValue && <span className="bg-info-soft ml-2 rounded-md px-2 py-0.5 text-xs font-normal">{t('seqwf.setup.unsavedRole')}</span>}</h3>
            {roleNodes(role.name).map(n => <p key={n.id} className="text-text-muted mt-1 text-xs">{`${n.id} · ${n.responsibilities.join(' ')}`}</p>)}
            <p className="text-text-muted mt-1 text-xs">{t('seqwf.roleLine', { eligibility: role.eligibility, independent: role.independentOf.join(', ') || '—' })}</p>
          </div>
          <div className="min-w-0">
            <label htmlFor={id} className="text-text-muted text-xs">{t('seqwf.setup.defaultAgent')}</label>
            <select id={id} value={value} disabled={locked} aria-invalid={clashes.length > 0 || undefined}
              aria-describedby={clashes.length ? `${id}-clash` : undefined}
              onChange={e => edit(role.name, e.target.value)}
              className="bg-surface-sunken border-border-default text-text-primary mt-1 w-full min-w-0 rounded-lg border px-2 py-1 text-sm disabled:opacity-70">
              <option value="">{t('seqwf.setup.chooseAgent')}</option>
              {choicesFor(role.name).map(name => <option key={name} value={name}>{agentTeam(name) ? `${name} · ${agentTeam(name)}` : name}</option>)}
            </select>
            {clashes.length > 0 && <p id={`${id}-clash`} className="text-feedback-danger mt-1 text-xs">{t('seqwf.setup.clash', { roles: clashes.join(', ') })}</p>}
          </div>
        </li>;
      })}</ul>
      <div className="border-border-default flex flex-wrap items-center gap-2 border-t px-4 py-3">
        <Button size="sm" onClick={() => void save()} aria-disabled={saveBlocked} className="aria-disabled:opacity-50">
          {t(sending ? 'seqwf.setup.saving' : 'seqwf.setup.save')}</Button>
        <Button size="sm" variant="outline" onClick={discard} aria-disabled={locked || !draft.dirty} className="aria-disabled:opacity-50">{t('seqwf.setup.discard')}</Button>
        <span className="text-text-muted text-xs">{t('seqwf.setup.saveNote')}</span>
      </div>
      {!locked && (incomplete || clash || conflictPending) && <p className="text-text-muted px-4 pb-3 text-xs">{t(conflictPending ? 'seqwf.setup.blockedConflict' : incomplete ? 'seqwf.setup.blockedIncomplete' : 'seqwf.setup.blockedClash')}</p>}
    </section>}

    <section aria-labelledby="seqwf-setup-admission" className="border-border-default bg-bg-raised rounded-lg border px-4 py-3 text-sm">
      <h2 id="seqwf-setup-admission" className="text-text-primary font-medium">{t('seqwf.setup.newRuns')}</h2>
      <p className="mt-1">{t('seqwf.setup.admissionBody')}</p>
    </section>
  </div>;
}

/**
 * Records an own committed receipt: keeps the highest known commit, and bases the
 * draft on it (never lowering an already higher known basis). The read cache is untouched.
 */
function withCommit(m: Memory, identity: string, outcome: Extract<Outcome, { kind: 'saved' | 'confirmed' }>): Memory {
  const committed = newer(m.committed, identity, outcome.receipt.original);
  return { ...m, outcome, committed,
    draft: m.draft ? { ...m.draft, basis: Math.max(m.draft.basis, committed.settings.generation), dirty: false } : m.draft };
}

/** Keeps the highest committed generation known for this definition identity. */
function newer(prev: Committed | null, identity: string, settings: Settings): Committed {
  return prev?.identity === identity && prev.settings.generation >= settings.generation ? prev : { identity, settings };
}

/** A definite refusal the provider already released (no fence remains). */
function actionsReleased(code: string): boolean {
  return ['sequential_settings_request_invalid', 'sequential_definition_not_found', 'sequential_definition_conflict',
    'sequential_settings_defaults_invalid', 'sequential_authority_denied'].includes(code);
}

function BindingList({ bindings }: { bindings: readonly Binding[] }): JSX.Element {
  return <dl className="mt-2 flex flex-col gap-1 text-sm">
    {bindings.map(b => <div key={b.role} className="flex min-w-0 flex-wrap gap-x-3"><dt className="text-text-muted w-32 shrink-0">{b.role}</dt><dd className="min-w-0 font-mono wrap-anywhere">{b.principal}</dd></div>)}
  </dl>;
}
