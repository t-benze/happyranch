/**
 * CodingSection — Founder coding configuration for one org (THR-320).
 *
 * The page reads the org's current coding settings view and saves the
 * complete canonical `workflow-coding-settings-command@1` replacement. The
 * server remains the authority for eligibility, membership, repository scope
 * and authority; roster hints here are advisory and grant no access.
 *
 * Save discipline:
 * - One explicit Save freezes one operation key and body in the org's send
 *   fence, owned by the settings provider so it survives leaving the pane and
 *   org A->B->A switches. Nothing is retried, rekeyed or reapplied, and fields
 *   are locked (render and handler) while the send is pending.
 * - 409 stale/conflict keeps the draft and focus; the refreshed version must be
 *   adopted explicitly before the next save.
 * - An uncertain outcome keeps the original key/body and offers only a
 *   read-only GET(original key) reconciliation. Only a reconciled committed
 *   receipt releases it; an absent key or a failed check never authorizes a
 *   new write.
 * - Unchanged mappings keep the last accepted mapping byte-for-byte, so disabling
 *   never forces replacement selections (disabled-retain).
 *
 * Saving configuration never enables coding execution.
 */
import { useEffect, useMemo, useState } from 'react';
import { Button } from '@/design-system/primitives/Button';
import { useAgentsList } from '@/hooks/agents';
import { useTeamsList } from '@/hooks/teams';
import { useIdentityPresentation } from '@/hooks/identities';
import { useTranslation } from '@/hooks/i18n';
import {
  useCodingSendFence,
  useCodingSettings,
  useCodingSettingsOptions,
  useCodingSettingsActions,
  type CodingAgentBinding,
  type CodingConfiguration,
  type CodingRoles,
  type CodingSettingsCommand,
  type CodingSettingsCurrent,
  type CodingSettingsReceipt,
} from '@/hooks/settings';
import { ApiError } from '@/lib/api';
import type { MessageKey } from '@/lib/i18n';

type RoleKey = keyof CodingRoles;
const ROLE_KEYS: readonly RoleKey[] = ['implementer', 'code_reviewer', 'qa_engineer', 'agent_manager'];
const ROLE_LABEL: Record<RoleKey, MessageKey> = {
  implementer: 'settings.coding.role.implementer',
  code_reviewer: 'settings.coding.role.codeReviewer',
  qa_engineer: 'settings.coding.role.qaEngineer',
  agent_manager: 'settings.coding.role.agentManager',
};
const ROLE_HELP: Record<RoleKey, MessageKey> = {
  implementer: 'settings.coding.roleHelp.implementer',
  code_reviewer: 'settings.coding.roleHelp.codeReviewer',
  qa_engineer: 'settings.coding.roleHelp.qaEngineer',
  agent_manager: 'settings.coding.roleHelp.agentManager',
};

type Admission = 'disabled' | 'explicit';
type Basis = { generation: number; mapping_generation: number };
interface Draft {
  admission: Admission;
  roles: Record<RoleKey, string>;
  basis: Basis;
  release: CodingConfiguration['release'] | null;
  authority: CodingConfiguration['authority'] | null;
  repositories: (Omit<CodingConfiguration['repository_references'][number], 'object_format'> & {
    object_format: '' | 'sha1' | 'sha256';
  })[];
}

type Hint = 'notWorker' | 'notManager' | 'notRegisteredManager' | 'noExecutor' | 'noTeam' | 'missingRepository';
const HINT_KEY: Record<Hint, MessageKey> = {
  notWorker: 'settings.coding.hint.notWorker',
  notManager: 'settings.coding.hint.notManager',
  notRegisteredManager: 'settings.coding.hint.notRegisteredManager',
  noExecutor: 'settings.coding.hint.noExecutor',
  noTeam: 'settings.coding.hint.noTeam',
  missingRepository: 'settings.coding.hint.missingRepository',
};
/** Hints that make a NEW selection pointless; repository hints stay advisory. */
const BLOCKING_HINTS = new Set<Hint>(['notWorker', 'notManager', 'notRegisteredManager', 'noExecutor', 'noTeam']);

interface Candidate {
  name: string;
  team: string | null;
  role: 'manager' | 'worker' | null;
  executor: string | null;
  repos: Record<string, string>;
}

type Outcome =
  | { kind: 'none' }
  | { kind: 'saved'; receipt: CodingSettingsReceipt; current: CodingSettingsCurrent }
  | { kind: 'conflict'; code: string; current: CodingSettingsCurrent | null }
  | { kind: 'refused'; status: number; code: string | null };

type Reconcile =
  | { kind: 'none' }
  | { kind: 'reading' }
  | { kind: 'committed'; receipt: CodingSettingsReceipt; current: CodingSettingsCurrent }
  | { kind: 'absent' }
  | { kind: 'error'; detail: string };

const CONFLICT_CODES = new Set(['coding_settings_generation_stale', 'coding_settings_operation_conflict']);

function errorCode(error: unknown): string | null {
  return error instanceof ApiError ? error.code : null;
}

function conflictCurrent(error: ApiError): CodingSettingsCurrent | null {
  const detail = error.detail;
  if (detail && typeof detail === 'object' && 'current' in detail) {
    const current = (detail as { current: unknown }).current;
    if (current && typeof current === 'object') return current as CodingSettingsCurrent;
  }
  return null;
}

/** A definite server refusal published nothing; every other failure is uncertain. */
function isUncertain(error: unknown): boolean {
  if (!(error instanceof ApiError)) return true;
  if (error.status === 503) return error.code !== 'coding_settings_invalid' && error.code !== 'coding_settings_preview_only';
  return error.status >= 500;
}

function rolesOf(configuration: CodingConfiguration | null): Record<RoleKey, string> {
  return {
    implementer: configuration?.roles.implementer.principal ?? '',
    code_reviewer: configuration?.roles.code_reviewer.principal ?? '',
    qa_engineer: configuration?.roles.qa_engineer.principal ?? '',
    agent_manager: configuration?.roles.agent_manager.principal ?? '',
  };
}

function draftFrom(current: CodingSettingsCurrent, configuration: CodingConfiguration | null): Draft {
  return {
    admission: current.admission,
    roles: rolesOf(configuration),
    basis: { generation: current.generation, mapping_generation: current.mapping_generation },
    release: configuration?.release ?? null,
    authority: configuration?.authority ?? null,
    repositories: configuration?.repository_references ?? [],
  };
}

function newOperationKey(): string {
  return `coding-settings:${crypto.randomUUID()}`;
}

function shortDigest(digest: string | null | undefined): string {
  return digest ? `${digest.slice(0, 12)}…` : '—';
}

function sameMetadata(draft: Draft, options: { authority: Draft['authority']; release: Draft['release'] }): boolean {
  return draft.authority?.namespace === options.authority?.namespace
    && draft.authority?.generation === options.authority?.generation
    && draft.authority?.snapshot_digest === options.authority?.snapshot_digest
    && draft.release?.identity === options.release?.identity
    && draft.release?.version === options.release?.version
    && draft.release?.definition_digest === options.release?.definition_digest;
}

export function CodingSection(): JSX.Element {
  const { t } = useTranslation();
  const query = useCodingSettings();
  const optionsQuery = useCodingSettingsOptions();
  const actions = useCodingSettingsActions();
  const fence = useCodingSendFence();
  const agentsQuery = useAgentsList();
  const teamsQuery = useTeamsList();
  const { label } = useIdentityPresentation();

  const view = query.data;
  const configuration = view?.configuration ?? null;
  const current = view?.current;
  // Errors/refreshes revoke usability of even cached options. They never edit
  // the draft, touch settings receipts, release a fence or cause a send.
  const options = !optionsQuery.isError && !optionsQuery.isFetching
    && optionsQuery.data?.org_slug === view?.org_slug ? optionsQuery.data : undefined;

  const [draft, setDraft] = useState<Draft | null>(null);
  const [dirty, setDirty] = useState(false);
  const [outcome, setOutcome] = useState<Outcome>({ kind: 'none' });
  const [notice, setNotice] = useState<MessageKey | null>(null);
  const [reconcileState, setReconcileState] = useState<{ key: string; state: Reconcile }>({ key: '', state: { kind: 'none' } });

  // An untouched draft follows the authoritative view; an edited draft never
  // changes underneath the Founder (stale-conflict retention).
  useEffect(() => {
    if (!current) return;
    if (!dirty) setDraft(draftFrom(current, configuration));
  }, [current, configuration, dirty]);

  const candidates: Candidate[] = useMemo(
    () => (agentsQuery.data?.agents ?? []).map((a) => ({
      name: a.name, team: a.team, role: a.role, executor: a.executor, repos: a.repos ?? {},
    })),
    [agentsQuery.data],
  );
  const registeredManagers = useMemo(() => new Set(
    (teamsQuery.data?.teams ?? [])
      .filter((team) => team.manager && team.manager_kind !== 'human')
      .map((team) => team.manager as string),
  ), [teamsQuery.data]);
  const repoKeys = useMemo(
    () => (draft?.repositories ?? []).map((r) => r.repo_key),
    [draft?.repositories],
  );

  const hintsFor = (role: RoleKey, c: Candidate): Hint[] => {
    const hints: Hint[] = [];
    if (role === 'agent_manager') {
      if (c.role !== 'manager') hints.push('notManager');
      else if (teamsQuery.data && !registeredManagers.has(c.name)) hints.push('notRegisteredManager');
    } else if (c.role !== 'worker') {
      hints.push('notWorker');
    }
    if (!c.executor) hints.push('noExecutor');
    if (!c.team) hints.push('noTeam');
    if (repoKeys.some((key) => !(key in c.repos))) hints.push('missingRepository');
    return hints;
  };

  if (query.isLoading) {
    return <p role="status" className="text-text-secondary text-sm">{t('settings.coding.loading')}</p>;
  }
  if (query.isError || !view || !current || !draft) {
    const err = query.error;
    const unsupported = err instanceof ApiError && (err.status === 404 || err.status === 405) && !err.code;
    return (
      <div role="alert" className="border-tier-red bg-feedback-danger/10 text-tier-red rounded border p-3 text-sm">
        <p>{t(unsupported ? 'settings.coding.unsupported' : 'settings.coding.loadError')}</p>
        {errorCode(err) && <p className="mt-1 font-mono text-xs break-all">{errorCode(err)}</p>}
        <Button size="sm" variant="secondary" className="mt-2" onClick={() => void query.refetch()}>
          {t('settings.coding.retry')}
        </Button>
      </div>
    );
  }

  const rolesChanged = configuration === null || ROLE_KEYS.some((r) => draft.roles[r] !== configuration.roles[r].principal);
  const repositoriesChanged = JSON.stringify(draft.repositories) !== JSON.stringify(configuration?.repository_references ?? []);
  const metadataChanged = !sameMetadata(draft, configuration ?? { release: null, authority: null });
  const retain = configuration !== null && !rolesChanged && !repositoriesChanged && !metadataChanged;
  const disabledRetain = retain && draft.admission === 'disabled';
  const optionsReady = !!options?.release && !!options.authority && options.repository_choices.length > 0;
  const currentMetadata = optionsReady && sameMetadata(draft, options);
  const basisStale = draft.basis.generation !== current.generation
    || draft.basis.mapping_generation !== current.mapping_generation;
  const saving = fence?.phase === 'sending';
  const locked = fence !== null;
  const reconcileOf: Reconcile = fence && reconcileState.key === fence.command.operation_key
    ? reconcileState.state : { kind: 'none' };
  const selected = ROLE_KEYS.map((r) => draft.roles[r]);
  const distinct = selected.every((p) => p !== '') && new Set(selected).size === ROLE_KEYS.length;

  // A changed mapping binds every role to its CURRENT actual team; the server
  // then judges current eligibility. An agent missing from the roster keeps
  // its accepted binding for that judgement. An unchanged mapping is sent as
  // the accepted mapping itself (disabled-retain), never rebuilt.
  const binding = (role: RoleKey): CodingAgentBinding => {
    const principal = draft.roles[role];
    const agent = candidates.find((c) => c.name === principal);
    if (agent?.team) return { principal, team: agent.team };
    if (configuration && configuration.roles[role].principal === principal) return configuration.roles[role];
    return { principal, team: '' };
  };

  const validation = (): MessageKey | null => {
    if (basisStale) return 'settings.coding.adoptFirst';
    if (!distinct) return 'settings.coding.distinctRequired';
    if (!disabledRetain && !optionsReady) return 'settings.coding.options.unavailable';
    if (!disabledRetain && !currentMetadata) return 'settings.coding.options.adoptFirst';
    if (draft.repositories.length < 1 || draft.repositories.length > 8
      || draft.repositories.some((r) => !r.object_format)) return 'settings.coding.repositories.required';
    if (rolesChanged && ROLE_KEYS.some((r) => configuration?.roles[r].principal !== draft.roles[r]
      && !candidates.some((c) => c.name === draft.roles[r] && c.team))) return 'settings.coding.selectionRequired';
    return null;
  };

  const edit = (next: Partial<Omit<Draft, 'basis'>>) => {
    // Handler-level lock: a disabled appearance is not the only guard.
    if (actions.fence()) return;
    setDraft((d) => (d ? { ...d, ...next, roles: next.roles ?? d.roles } : d));
    setDirty(true);
    setNotice(null);
  };

  const save = async () => {
    if (actions.fence()) return;
    const problem = validation();
    if (problem) { setNotice(problem); return; }
    // Validation above has established the explicit complete draft. Retaining
    // an unchanged Mapping sends the accepted object itself, byte-for-byte.
    if (!draft.release || !draft.authority) return;
    const mapping: CodingConfiguration = !retain
      ? { release: draft.release, authority: draft.authority,
        eligible_replacements: configuration?.eligible_replacements ?? {
          implementer: [], code_reviewer: [], qa_engineer: [], agent_manager: [],
        },
        repository_references: draft.repositories as CodingConfiguration['repository_references'], roles: {
        implementer: binding('implementer'),
        code_reviewer: binding('code_reviewer'),
        qa_engineer: binding('qa_engineer'),
        agent_manager: binding('agent_manager'),
      } }
      : configuration!;
    const command: CodingSettingsCommand = {
      format: 'workflow-coding-settings-command@1',
      org_slug: view.org_slug,
      operation_key: newOperationKey(),
      expected_generation: draft.basis.generation,
      expected_mapping_generation: draft.basis.mapping_generation,
      admission: draft.admission,
      mapping,
    };
    setNotice(null);
    setOutcome({ kind: 'none' });
    // The provider freezes the fence synchronously inside save(), before its
    // first await. The continuation below still settles that SAME org/key if
    // this pane has since been detached (its React updates are then ignored).
    try {
      const result = await actions.save(command);
      setOutcome({ kind: 'saved', receipt: result.operation, current: result.current });
      setDraft(draftFrom(result.current, result.configuration));
      setDirty(false);
    } catch (error) {
      if (isUncertain(error)) {
        actions.settle(command.operation_key, errorCode(error) ?? (error instanceof Error ? error.message : String(error)));
      } else {
        actions.settle(command.operation_key, null);
        if (error instanceof ApiError && error.status === 409 && CONFLICT_CODES.has(error.code ?? '')) {
          setOutcome({ kind: 'conflict', code: error.code ?? '', current: conflictCurrent(error) });
          void query.refetch();
        } else {
          setOutcome({ kind: 'refused', status: error instanceof ApiError ? error.status : 0, code: errorCode(error) });
        }
      }
    }
  };

  const reconcile = async () => {
    if (!fence || fence.phase !== 'uncertain' || reconcileOf.kind === 'reading') return;
    const key = fence.command.operation_key;
    setReconcileState({ key, state: { kind: 'reading' } });
    let next: Reconcile;
    try {
      const result = await actions.readOperation(key);
      next = result.operation
        ? { kind: 'committed', receipt: result.operation, current: result.current }
        : { kind: 'error', detail: 'operation_missing' };
    } catch (error) {
      next = error instanceof ApiError && error.status === 404 && error.code === 'coding_settings_operation_not_found'
        ? { kind: 'absent' }
        : { kind: 'error', detail: errorCode(error) ?? (error instanceof Error ? error.message : String(error)) };
    }
    setReconcileState({ key, state: next });
  };

  // Only a reconciled committed receipt for the original key releases the
  // fence. An absent key or a failed check keeps the page read-only: neither
  // proves the original send can never commit.
  const acceptCommitted = () => {
    if (!fence || reconcileOf.kind !== 'committed') return;
    const settled = reconcileOf;
    actions.resolveCommitted(fence.command.operation_key);
    setOutcome({ kind: 'saved', receipt: settled.receipt, current: settled.current });
    setDirty(false);
  };

  const adoptCurrent = () => {
    if (actions.fence()) return;
    setDraft((d) => (d ? { ...d, basis: { generation: current.generation, mapping_generation: current.mapping_generation } } : d));
    setOutcome({ kind: 'none' });
    setNotice(null);
  };

  const discard = () => {
    if (actions.fence()) return;
    setDraft(draftFrom(current, configuration));
    setDirty(false);
    setNotice(null);
    setOutcome({ kind: 'none' });
  };

  const adoptOptions = () => {
    if (actions.fence() || !optionsReady || !options) return;
    edit({ release: options.release, authority: options.authority });
  };
  const repositoryChoices = [...new Map([
    ...draft.repositories.map((r) => [r.repository_id, r] as const),
    ...(options?.repository_choices ?? []).map((r) => [r.repository_id, r] as const),
  ]).values()];

  const saveBlocked = locked;

  return (
    <section className="min-w-0 space-y-4 break-words" aria-label={t('settings.panel.coding.title')}>

      {/* Honest readiness: configuration is never engine readiness. */}
      <div className="border-border-default bg-surface-sunken rounded-md border p-3 text-sm" data-testid="coding-readiness">
        <p className="font-medium">{t('settings.coding.readiness.title')}</p>
        <p className="text-text-secondary mt-1">{t('settings.coding.readiness.body')}</p>
      </div>

      <dl className="border-border-default grid grid-cols-1 gap-x-4 gap-y-1 rounded-md border p-3 text-sm sm:grid-cols-2" data-testid="coding-current">
        <dt className="text-text-secondary">{t('settings.coding.current.generation')}</dt>
        <dd className="font-mono">{current.generation}</dd>
        <dt className="text-text-secondary">{t('settings.coding.current.admission')}</dt>
        <dd>{t(current.admission === 'explicit' ? 'settings.coding.admission.explicit' : 'settings.coding.admission.disabled')}</dd>
        <dt className="text-text-secondary">{t('settings.coding.current.mappingGeneration')}</dt>
        <dd className="font-mono">{current.mapping_generation}</dd>
        <dt className="text-text-secondary">{t('settings.coding.current.release')}</dt>
        <dd className="font-mono break-all">
          {current.release ? `${current.release.identity} v${current.release.version}` : t('settings.coding.none')}
        </dd>
        <dt className="text-text-secondary">{t('settings.coding.current.snapshot')}</dt>
        <dd className="font-mono break-all">{shortDigest(current.snapshot_digest)}</dd>
      </dl>

      {configuration === null && (
        <div className="border-border-default rounded-md border p-3 text-sm" data-testid="coding-initial">
          <p className="font-medium">{t('settings.coding.initial.title')}</p>
          <p className="text-text-secondary mt-1">{t('settings.coding.initial.body')}</p>
        </div>
      )}

      {query.isFetching && <p role="status" className="text-text-secondary text-xs">{t('settings.coding.refreshing')}</p>}

      <div className="border-border-default space-y-2 rounded-md border p-3 text-sm" data-testid="coding-options">
        <p className="font-medium">{t('settings.coding.options.title')}</p>
        <p className="text-text-secondary">{t('settings.coding.options.help')}</p>
        {optionsQuery.isFetching && <p role="status">{t('settings.coding.options.loading')}</p>}
        {optionsQuery.isError && <p className="text-feedback-danger">{t('settings.coding.options.error')}</p>}
        {!optionsQuery.isFetching && !optionsQuery.isError && !optionsReady && <p>{t('settings.coding.options.unavailable')}</p>}
        {optionsReady && options && <p className="font-mono break-all">{t('settings.coding.options.pin', {
          generation: options.authority!.generation, digest: shortDigest(options.authority!.snapshot_digest),
        })}</p>}
        {draft.authority && <p className="font-mono break-all">{t('settings.coding.options.draftPin', {
          generation: draft.authority.generation, digest: shortDigest(draft.authority.snapshot_digest),
        })}</p>}
        <div className="flex flex-wrap gap-2">
          <Button size="sm" variant="secondary" onClick={() => void optionsQuery.refetch()} disabled={optionsQuery.isFetching}>
            {t('settings.coding.options.refresh')}
          </Button>
          <Button size="sm" variant="secondary" data-testid="coding-adopt-options" onClick={adoptOptions} disabled={locked || !optionsReady}>
            {t('settings.coding.options.adopt')}
          </Button>
        </div>
      </div>

      <fieldset className="space-y-2" disabled={locked}>
        <legend className="mb-1 text-sm font-medium">{t('settings.coding.repositories.title')}</legend>
        <p className="text-text-secondary text-xs">{t('settings.coding.repositories.help')}</p>
        {repositoryChoices.map((choice) => {
          const selectedRepo = draft.repositories.find((r) => r.repository_id === choice.repository_id);
          return <div key={choice.repository_id} className="border-border-default space-y-1 rounded-md border p-2 text-sm">
            <label className="flex items-center gap-2">
              <input type="checkbox" checked={!!selectedRepo} aria-label={choice.repo_key}
                disabled={!selectedRepo && !optionsReady}
                onChange={() => {
                  if (actions.fence()) return;
                  if (selectedRepo) edit({ repositories: draft.repositories.filter((r) => r.repository_id !== choice.repository_id) });
                  else if (optionsReady && draft.repositories.length < 8) edit({ repositories: [...draft.repositories, { ...choice, object_format: '' }] });
                  else setNotice('settings.coding.repositories.required');
                }} />
              {choice.repo_key}
            </label>
            <p className="text-text-secondary font-mono text-xs break-all">{choice.repository_id}</p>
            {selectedRepo && <label className="block">
              {t('settings.coding.repositories.format', { repository: choice.repo_key })}
              <select value={selectedRepo.object_format} className="bg-surface-sunken border-border-default ml-2 rounded border px-2 py-1"
                onChange={(e) => {
                  const value = e.target.value;
                  if (value !== '' && value !== 'sha1' && value !== 'sha256') return;
                  edit({ repositories: draft.repositories.map((r) => r.repository_id === choice.repository_id ? { ...r, object_format: value } : r) });
                }}>
                <option value="">{t('settings.coding.repositories.chooseFormat')}</option>
                {[...new Set([selectedRepo.object_format, ...(options?.object_formats ?? [])])]
                  .filter((format) => format !== '')
                  .map((format) => <option key={format} value={format}>{format}</option>)}
              </select>
            </label>}
          </div>;
        })}
        {configuration === null && <p className="text-text-secondary text-xs">{t('settings.coding.repositories.emptyReplacements')}</p>}
      </fieldset>

      <fieldset className="space-y-2" disabled={locked}>
        <legend className="mb-1 text-sm font-medium">{t('settings.coding.admission.heading')}</legend>
        {(['disabled', 'explicit'] as const).map((value) => (
          <label key={value} className="flex items-start gap-2 text-sm">
            <input
              type="radio"
              name="coding-admission"
              value={value}
              checked={draft.admission === value}
              onChange={() => edit({ admission: value })}
              className="mt-1"
            />
            <span>
              {t(value === 'explicit' ? 'settings.coding.admission.explicit' : 'settings.coding.admission.disabled')}
              <span className="text-text-secondary block text-xs">
                {t(value === 'explicit' ? 'settings.coding.admission.explicitHelp' : 'settings.coding.admission.disabledHelp')}
              </span>
            </span>
          </label>
        ))}
      </fieldset>

      <fieldset className="space-y-3" disabled={locked}>
        <legend className="mb-1 text-sm font-medium">{t('settings.coding.roles.heading')}</legend>
        <p className="text-text-secondary text-xs">{t('settings.coding.roles.help')}</p>
        {agentsQuery.isLoading && <p role="status" className="text-text-secondary text-xs">{t('settings.coding.roster.loading')}</p>}
        {agentsQuery.isError && <p role="alert" className="text-feedback-danger text-xs">{t('settings.coding.roster.error')}</p>}
        {ROLE_KEYS.map((role) => {
          const id = `coding-role-${role}`;
          const value = draft.roles[role];
          const chosen = candidates.find((c) => c.name === value);
          const accepted = configuration?.roles[role];
          const chosenHints = chosen ? hintsFor(role, chosen) : [];
          return (
            <div key={role} className="border-border-default min-w-0 rounded-md border p-3">
              <label htmlFor={id} className="block text-sm font-medium">{t(ROLE_LABEL[role])}</label>
              <p id={`${id}-help`} className="text-text-secondary mb-2 text-xs">{t(ROLE_HELP[role])}</p>
              <select
                id={id}
                aria-describedby={`${id}-help ${id}-detail`}
                value={value}
                onChange={(e) => edit({ roles: { ...draft.roles, [role]: e.target.value } })}
                className="bg-surface-sunken border-border-default text-text-primary w-full min-w-0 rounded-lg border px-2 py-1 text-sm"
              >
                <option value="">{t('settings.coding.roles.choose')}</option>
                {accepted && !candidates.some((c) => c.name === accepted.principal) && (
                  <option value={accepted.principal}>
                    {t('settings.coding.roles.retained', { label: label(accepted.principal), team: accepted.team })}
                  </option>
                )}
                {candidates.map((c) => {
                  const hints = hintsFor(role, c);
                  const blocked = hints.some((h) => BLOCKING_HINTS.has(h)) && c.name !== accepted?.principal;
                  return (
                    <option key={c.name} value={c.name} disabled={blocked}>
                      {t('settings.coding.roles.option', {
                        label: label(c.name),
                        team: c.team ?? '—',
                        role: c.role ?? '—',
                      })}
                      {blocked ? ` — ${t(HINT_KEY[hints.find((h) => BLOCKING_HINTS.has(h)) as Hint])}` : ''}
                    </option>
                  );
                })}
              </select>
              <div id={`${id}-detail`} className="text-text-secondary mt-1 text-xs">
                {value && (
                  <p className="font-mono break-all">
                    {t('settings.coding.roles.detail', {
                      id: value,
                      team: (accepted?.principal === value ? accepted.team : chosen?.team) ?? '—',
                    })}
                  </p>
                )}
                {chosenHints.map((h) => <p key={h} className="text-feedback-warning">{t(HINT_KEY[h])}</p>)}
                {value && !chosen && accepted?.principal === value && (
                  <p className="text-feedback-warning">{t('settings.coding.hint.notInRoster')}</p>
                )}
              </div>
            </div>
          );
        })}
        {configuration && (
          <p className="text-text-secondary text-xs" data-testid="coding-preserved">
            {t('settings.coding.preserved', {
              replacements: ROLE_KEYS.reduce((n, r) => n + configuration.eligible_replacements[r].length, 0),
              repositories: configuration.repository_references.length,
            })}
          </p>
        )}
      </fieldset>

      {basisStale && outcome.kind !== 'conflict' && dirty && !locked && (
        <div role="status" className="border-border-default rounded-md border p-3 text-sm">
          <p>{t('settings.coding.newerVersion', { generation: current.generation, mapping: current.mapping_generation })}</p>
          <Button size="sm" variant="secondary" className="mt-2" onClick={adoptCurrent}>{t('settings.coding.adopt')}</Button>
        </div>
      )}

      {outcome.kind === 'conflict' && (
        <div role="alert" className="border-feedback-warning rounded-md border p-3 text-sm" data-testid="coding-conflict">
          <p>{t('settings.coding.conflict', {
            generation: outcome.current?.generation ?? current.generation,
            mapping: outcome.current?.mapping_generation ?? current.mapping_generation,
          })}</p>
          <p className="mt-1 font-mono text-xs break-all">{outcome.code}</p>
          <Button size="sm" variant="secondary" className="mt-2" onClick={adoptCurrent}>{t('settings.coding.adopt')}</Button>
        </div>
      )}

      {outcome.kind === 'refused' && (
        <div role="alert" className="border-tier-red bg-feedback-danger/10 text-tier-red rounded border p-3 text-sm" data-testid="coding-refused">
          <p>{t('settings.coding.refused', { status: outcome.status })}</p>
          {outcome.code && <p className="mt-1 font-mono text-xs break-all">{outcome.code}</p>}
        </div>
      )}

      {fence?.phase === 'sending' && (
        <div role="status" className="border-border-default rounded-md border p-3 text-sm" data-testid="coding-sending">
          <p>{t('settings.coding.sending')}</p>
          <p className="mt-1 font-mono text-xs break-all">{fence.command.operation_key}</p>
        </div>
      )}

      {fence?.phase === 'uncertain' && (
        <div role="alert" className="border-feedback-warning rounded-md border p-3 text-sm" data-testid="coding-uncertain">
          <p>{t('settings.coding.uncertain')}</p>
          <p className="mt-1 font-mono text-xs break-all">{fence.command.operation_key}</p>
          <p className="mt-1 font-mono text-xs break-all">{fence.detail}</p>
          {reconcileOf.kind === 'committed' && (
            <p className="mt-2">{t('settings.coding.reconcile.committed', {
              receipt: reconcileOf.receipt.snapshot.generation,
              current: reconcileOf.current.generation,
            })}</p>
          )}
          {reconcileOf.kind === 'absent' && <p className="mt-2">{t('settings.coding.reconcile.absent')}</p>}
          {reconcileOf.kind === 'error' && (
            <p className="mt-2">{t('settings.coding.reconcile.error', { detail: reconcileOf.detail })}</p>
          )}
          <div className="mt-2 flex flex-wrap gap-2">
            <Button size="sm" variant="secondary" onClick={() => void reconcile()} aria-disabled={reconcileOf.kind === 'reading'}>
              {t(reconcileOf.kind === 'reading' ? 'settings.coding.reconcile.reading' : 'settings.coding.reconcile.action')}
            </Button>
            {reconcileOf.kind === 'committed' && (
              <Button size="sm" variant="secondary" onClick={acceptCommitted}>
                {t('settings.coding.reconcile.accept')}
              </Button>
            )}
          </div>
        </div>
      )}

      {outcome.kind === 'saved' && (
        <div role="status" className="border-tier-green bg-feedback-success/10 text-tier-green rounded border p-3 text-sm" data-testid="coding-saved">
          <p>{t('settings.coding.saved', {
            receipt: outcome.receipt.snapshot.generation,
            current: outcome.current.generation,
            mapping: outcome.current.mapping_generation,
          })}</p>
          <p className="mt-1 font-mono text-xs break-all">{outcome.receipt.operation_key}</p>
        </div>
      )}

      {notice && <p role="alert" className="text-feedback-danger text-sm">{t(notice)}</p>}

      <div className="flex flex-wrap gap-2">
        <Button
          size="sm"
          onClick={() => void save()}
          aria-disabled={saveBlocked}
          className="aria-disabled:opacity-50"
          data-testid="coding-save"
        >
          {t(saving ? 'settings.coding.saving' : 'settings.coding.save')}
        </Button>
        <Button size="sm" variant="secondary" onClick={discard} aria-disabled={saveBlocked || !dirty} className="aria-disabled:opacity-50">
          {t('settings.coding.discard')}
        </Button>
      </div>
    </section>
  );
}
