/**
 * AddAgentDialog — the ONE shared Founder creation form, opened in two modes
 * (THR-402; PRD docs/product/prds/2026-11-26-team-and-agent-creation.md):
 *
 *   - Create team (`mode="team"`): Role is fixed to Manager; the form asks for
 *     the new team name and its manager. Submits one manager enrollment with
 *     `new_team`.
 *   - Create agent (`mode="agent"`): Worker by default, with an explicit
 *     existing-team chooser (agent-led teams and the Founder-managed Default;
 *     no implicit first-team selection). Choosing Manager says "Creates a new
 *     team" and switches to the manager-plus-team fields; returning to Worker
 *     never submits the hidden `new_team`.
 *
 * Rules live in `./createAgentForm` (pure; checked by
 * `web/scripts/create-agent-form.test.mjs`). The team suggestion follows the
 * manager name until the user edits it; a role change never overwrites an
 * edited team name, and an executor the user picked is never silently
 * replaced when the live list changes.
 *
 * Submission (A8/A9) goes through the existing config-owner machinery
 * (`useConfigOwner`, page `agents.create`): single-flight per owner, the
 * submitted body frozen, the create request never re-sent on a 401
 * (`replayOnUnauthorized: false` in `lib/api/agents#createAgent`). Outcomes:
 *   - success → open the returned canonical agent, refresh roster/teams/
 *     identities; "Created; refresh unavailable" when that readback fails;
 *     Create team then offers Create agent in the new team.
 *   - a confirmed refusal (4xx) → the matching field explains it, other input
 *     stays, correct and resubmit.
 *   - `agent_setup_incomplete` → "Agent created; setup incomplete", View agent
 *     and the supported `happyranch init-agent` recovery; no second Create.
 *   - a change native provably did not forward (typed pre-forward error) →
 *     "Not sent": draft kept, no unknown owner, explicit resubmit only.
 *   - no trustworthy answer → "Result unknown" and Check result, which retires
 *     in-flight reads and re-reads the roster and identities of the ORIGINAL
 *     org (`lib/freshReads`). A name match alone is reported as such, never as
 *     success; a new Create is possible only once nothing with that name is
 *     found AND the naming observations are complete. Nothing is retried or
 *     deleted.
 * A pending/unknown owner holds close, navigation and org/target switches
 * (owner gate). On a remote Host the form stays visible but read-only
 * ("Change this on {Host}"), and a native `remote_read_only` refusal keeps
 * the draft.
 */
import { useEffect, useMemo, useRef, useState, type FormEvent } from 'react';
import { Link, useNavigate, useParams } from 'react-router-dom';
import { useQueryClient } from '@tanstack/react-query';
import { Button } from '@/design-system/primitives/Button';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/design-system/primitives/Dialog';
import { Input } from '@/design-system/primitives/Input';
import { Label } from '@/design-system/primitives/Label';
import { Textarea } from '@/design-system/primitives/Textarea';
import { useAgentsList, useCreateAgent } from '@/hooks/agents';
import { useIdentities } from '@/hooks/identities';
import { useTeamsList } from '@/hooks/teams';
import { useExecutorOptions } from '@/hooks/executor-options';
import { useTranslation } from '@/hooks/i18n';
import type { MessageKey } from '@/lib/i18n';
import type { AgentSummary, IdentityList } from '@/lib/api/types';
import { REMOTE_OPS, useRemoteReadOnly, useRemoteRefusal } from '@/lib/remotePolicy';
import { RemoteReadOnlyNote } from '@/shared/remotePolicy/RemoteReadOnlyNote';
import { ownerContextParams, useConfigOwner, useOwnerGate } from '@/shared/owners';
import { freshReads } from '@/lib/freshReads';
import { classifyAgentError, renderAgentError, type AgentErrorView } from './strings';
import {
  agentNameProblem,
  buildCreateBody,
  classifyCreateError,
  fieldForCode,
  findCreatedAgent,
  newTeamProblem,
  suggestTeamName,
  type CheckFinding,
  type CreateBody,
  type CreateField,
  type CreateMode,
  type CreateRole,
} from './createAgentForm';

interface Props {
  open: boolean;
  /** Which action opened the form. Ignored while a sent create is unresolved. */
  mode: CreateMode;
  onOpenChange: (open: boolean) => void;
}

const FIELD_IDS: Record<CreateField, string> = {
  name: 'agent-name',
  team: 'agent-team',
  newTeam: 'agent-new-team',
  executor: 'agent-executor',
  description: 'agent-description',
  systemPrompt: 'agent-system-prompt',
};

type Phase =
  | { kind: 'edit' }
  | { kind: 'created'; mode: CreateMode; canonicalId: string; name: string; team: string }
  | { kind: 'setupIncomplete'; canonicalId: string | null; name: string; team: string }
  | { kind: 'found'; canonicalId: string; name: string };

interface Sent {
  body: CreateBody;
  slug: string;
  mode: CreateMode;
}

const SELECT_CLASS =
  'border-border-subtle bg-surface-sunken w-full rounded-sm border p-2 text-sm disabled:opacity-60';

export function AddAgentDialog({ open, mode: requestedMode, onOpenChange }: Props): JSX.Element {
  const { slug } = useParams<{ slug: string }>();
  const navigate = useNavigate();
  const qc = useQueryClient();
  const { t, render } = useTranslation();
  const teamsQuery = useTeamsList();
  const agentsQuery = useAgentsList();
  const identitiesQuery = useIdentities();
  const teams = useMemo(() => teamsQuery.data?.teams ?? [], [teamsQuery.data?.teams]);
  const teamsReady = !teamsQuery.isLoading && !teamsQuery.isError && teamsQuery.data !== undefined;
  const teamNames = useMemo(() => (teamsReady ? teams.map((entry) => entry.name) : null), [teamsReady, teams]);

  const [mode, setMode] = useState<CreateMode>(requestedMode);
  const [name, setName] = useState('');
  const [role, setRole] = useState<CreateRole>(requestedMode === 'team' ? 'manager' : 'worker');
  const [team, setTeam] = useState('');
  const [newTeam, setNewTeam] = useState('');
  const [teamEdited, setTeamEdited] = useState(false);
  const [executor, setExecutor] = useState('');
  const [executorPicked, setExecutorPicked] = useState(false);
  const [description, setDescription] = useState('');
  const [systemPrompt, setSystemPrompt] = useState('');
  const [touched, setTouched] = useState<ReadonlySet<CreateField>>(new Set());
  // A refusal the server tied to one field (cleared when that field changes),
  // or one for the whole form. Locale-neutral views re-translate in place.
  const [fieldError, setFieldError] = useState<{ field: CreateField; view: AgentErrorView } | null>(null);
  const [formError, setFormError] = useState<AgentErrorView | null>(null);
  const [phase, setPhase] = useState<Phase>({ kind: 'edit' });
  const [finding, setFinding] = useState<CheckFinding | null>(null);
  const sent = useRef<Sent | null>(null);
  const latch = useRef(false);

  const create = useCreateAgent();
  const executorOptions = useExecutorOptions();
  const remoteHost = useRemoteReadOnly(REMOTE_OPS.createAgent);
  const refusal = useRemoteRefusal();

  const reset = (next: CreateMode, preselectTeam = ''): void => {
    setMode(next);
    setRole(next === 'team' ? 'manager' : 'worker');
    setName('');
    setTeam(preselectTeam);
    setNewTeam('');
    setTeamEdited(false);
    setExecutor('');
    setExecutorPicked(false);
    setDescription('');
    setSystemPrompt('');
    setTouched(new Set());
    setFieldError(null);
    setFormError(null);
    setPhase({ kind: 'edit' });
    setFinding(null);
    sent.current = null;
    refusal.clear();
  };

  const editing = phase.kind === 'edit';
  const dirty =
    editing &&
    (name !== '' || team !== '' || description !== '' || systemPrompt !== '' || executorPicked ||
      (teamEdited && newTeam !== ''));
  const owner = useConfigOwner({
    page: 'agents.create',
    subject: sent.current?.body.display_name ?? (name || null),
    org: slug ?? null,
    dirty,
    draft: { mode, name, role, team, newTeam, executor, description, systemPrompt },
    onDiscard: () => reset(mode),
    checkResult: async () => {
      const submitted = sent.current;
      // Only the ORIGINAL org's reads issued AFTER this check started can
      // answer for this create (never a read that predates the attempt).
      if (submitted === null || submitted.slug !== slug) return 'unknown';
      const reads = await freshReads(qc, [['agents', submitted.slug], ['identities', submitted.slug]]);
      if (reads === null) return 'unknown';
      const [agentsData, identitiesData] = reads as [{ agents: AgentSummary[] }, IdentityList];
      const result = findCreatedAgent(submitted.body, agentsData.agents, identitiesData);
      setFinding(result);
      if (result.kind === 'matched') {
        setPhase({ kind: 'found', canonicalId: result.canonicalId, name: submitted.body.display_name });
        return 'saved';
      }
      return result.kind === 'absent' ? 'not_saved' : 'unknown';
    },
  });
  const status = owner.record?.status;
  const unknown = status === 'unknown';
  const busy = create.isPending || owner.busy || status === 'pending';
  const frozen = busy || unknown || !editing;

  // The opening action picks the mode, unless a sent create is unresolved
  // (that form stays exactly as it was submitted).
  const wasOpen = useRef(open);
  useEffect(() => {
    if (open && !wasOpen.current && !busy && !unknown) reset(requestedMode);
    wasOpen.current = open;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open]);

  // The draft belongs to the org it was opened for: an org change (after the
  // owner gate's consent) closes the form and starts over.
  const boundOrg = useRef(slug);
  if (!open && !busy && !unknown) boundOrg.current = slug;
  const orgChanged = open && slug !== boundOrg.current && !busy && !unknown;
  useEffect(() => {
    if (!orgChanged) return;
    boundOrg.current = slug;
    reset(requestedMode);
    onOpenChange(false);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [orgChanged]);

  // Executor: the first live option is the visible initial choice. A pick the
  // user made is never replaced; if it disappears it is shown as unavailable.
  const selectableNames = useMemo(
    () => new Set(executorOptions.selectable.map((o) => o.name)),
    [executorOptions.selectable],
  );
  useEffect(() => {
    if (executorPicked || frozen) return;
    if (executorOptions.state === 'ready') {
      if (!selectableNames.has(executor)) setExecutor(executorOptions.selectable[0]!.name);
    } else if (executorOptions.state === 'empty' && executor !== '') {
      setExecutor('');
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selectableNames, executorOptions.state, executorPicked, frozen]);

  const touch = (field: CreateField) =>
    setTouched((prev) => (prev.has(field) ? prev : new Set(prev).add(field)));
  // A field shows its "required" message only once the user has edited it
  // (never on blur: a message appearing on mousedown would move the control
  // being clicked).
  const clearFieldError = (field: CreateField) => {
    touch(field);
    if (fieldError?.field === field) setFieldError(null);
    setFormError(null);
    refusal.clear();
  };

  const onNameChange = (next: string) => {
    setName(next);
    clearFieldError('name');
    if (role === 'manager' && !teamEdited) setNewTeam(suggestTeamName(next));
  };
  const onNewTeamChange = (next: string) => {
    setNewTeam(next);
    setTeamEdited(true);
    clearFieldError('newTeam');
  };
  const onRoleChange = (next: CreateRole) => {
    setRole(next);
    setFieldError(null);
    setFormError(null);
    // The suggestion only fills an untouched team name.
    if (next === 'manager' && !teamEdited) setNewTeam(suggestTeamName(name));
  };

  // ---- validation (identical to the server's new-input rules) ----
  const nameProblem = agentNameProblem(name);
  const newTeamIssue = role === 'manager' ? newTeamProblem(newTeam, teamNames) : null;
  const teamStale = role === 'worker' && team !== '' && teamNames !== null && !teamNames.includes(team);
  const teamOk = role === 'worker' ? teamNames !== null && team !== '' && !teamStale : newTeamIssue === null;
  const executorStale = executorPicked && executorOptions.state !== 'loading' && !selectableNames.has(executor);
  const executorOk = executorOptions.state === 'ready' && selectableNames.has(executor);
  const descriptionOk = description.trim() !== '';
  const promptOk = systemPrompt.trim() !== '';
  const fieldsOk = nameProblem === null && teamOk && executorOk && descriptionOk && promptOk;
  // A server refusal tied to a field must be corrected before resubmitting.
  const canSubmit =
    editing && fieldsOk && fieldError === null && !frozen && remoteHost === null && !owner.saveRefused;

  const show = (field: CreateField, problem: boolean) => problem && touched.has(field);
  const nameMessage: MessageKey | null =
    nameProblem === 'invalid' ? 'agents.error.invalidName'
      : show('name', nameProblem === 'required') ? 'agents.create.error.nameRequired' : null;
  const newTeamMessage: MessageKey | null =
    newTeamIssue === 'reserved' ? 'agents.create.error.teamReserved'
      : newTeamIssue === 'invalid' ? 'agents.create.error.newTeamInvalid'
        : newTeamIssue === 'exists' ? 'agents.error.teamExists'
          : show('newTeam', newTeamIssue === 'required') ? 'agents.create.error.newTeamRequired' : null;
  const teamMessage: MessageKey | null =
    teamStale ? 'agents.create.error.teamStale'
      : show('team', role === 'worker' && team === '') ? 'agents.create.error.teamRequired' : null;
  const params = { name, team, newTeam, executor };
  const errorFor = (field: CreateField, local: MessageKey | null): string | null =>
    fieldError?.field === field
      ? renderAgentError(fieldError.view, t, sent.current
        ? { name: sent.current.body.display_name, team: sent.current.body.team ?? '', newTeam: sent.current.body.new_team ?? '', executor: sent.current.body.executor }
        : params)
      : local ? t(local, params) : null;

  const focusField = (field: CreateField) => {
    requestAnimationFrame(() => document.getElementById(FIELD_IDS[field])?.focus());
  };

  const onSubmit = async (event?: FormEvent) => {
    event?.preventDefault();
    // Single flight at the event boundary: a second click/Enter in the same
    // turn finds the latch (and owner.begin() refuses while one is in flight).
    if (!canSubmit || latch.current || owner.isBusy() || !slug) return;
    const ticket = owner.begin();
    if (ticket === null) return;
    latch.current = true;
    const body = buildCreateBody({ name, role, team, newTeam, executor, description, systemPrompt });
    sent.current = { body, slug, mode };
    refusal.clear();
    setFieldError(null);
    setFormError(null);
    setFinding(null);
    const result = await owner.track(
      () => create.mutateAsync(body),
      (error) => {
        const kind = classifyCreateError(error).kind;
        // A refusal or a provably not-forwarded change created nothing.
        return kind === 'refused' || kind === 'notSent' ? 'failed' : 'unknown';
      },
      ticket,
    );
    latch.current = false;
    if (result.ok) {
      const created = result.value;
      setPhase({
        kind: 'created',
        mode,
        canonicalId: created.canonical_id,
        name: created.addressable_name || body.display_name,
        team: created.team,
      });
      navigate(`/orgs/${slug}/agents/${encodeURIComponent(created.canonical_id)}`);
      return;
    }
    if (result.refused) return;
    // Native refused before forwarding: nothing was sent and the draft stays.
    if (refusal.note(result.error)) return;
    const outcome = classifyCreateError(result.error);
    if (outcome.kind === 'setupIncomplete') {
      // The enrollment committed: this create is resolved (never re-sent).
      owner.reconcile('saved');
      setPhase({
        kind: 'setupIncomplete',
        canonicalId: outcome.canonicalId,
        name: body.display_name,
        team: outcome.team ?? body.team ?? body.new_team ?? '',
      });
      return;
    }
    if (outcome.kind === 'notSent') {
      // Nothing left the app: the draft stays; only an explicit resubmit sends it.
      setFormError({ kind: 'message', key: 'agents.create.error.notSent' });
      return;
    }
    if (result.outcome === 'failed') {
      const code = outcome.kind === 'refused' ? outcome.code : null;
      const httpStatus = outcome.kind === 'refused' ? outcome.status : null;
      const field = fieldForCode(code);
      const view: AgentErrorView =
        httpStatus === 401 && code === null ? { kind: 'message', key: 'agents.create.error.authRenewed' }
          : code === 'invalid_team_name' ? { kind: 'message', key: 'agents.create.error.newTeamInvalid' }
            : code === 'team_reserved' ? { kind: 'message', key: 'agents.create.error.teamReserved' }
              : code === 'invalid_executor' ? { kind: 'message', key: 'agents.create.error.invalidExecutor' }
                : code === 'missing_required_field' ? { kind: 'message', key: 'agents.create.error.missingField' }
                  : classifyAgentError(result.error, 'agents.error.createFailed');
      if (code === 'unknown_team') void teamsQuery.refetch();
      if (code === 'team_exists') void teamsQuery.refetch();
      if (code === 'invalid_executor') executorOptions.retry();
      if (field) {
        setFieldError({ field, view });
        focusField(field);
      } else {
        setFormError(view);
      }
    }
    // Otherwise the owner holds the unknown result (rendered below).
  };

  // Close paths (close button, Escape, Cancel, Done) ask the owner gate first:
  // unsent edits get Keep editing / Discard; a sent create holds.
  const { requestOwnerTransition } = useOwnerGate();
  const requestOpenChange = (next: boolean): void => {
    if (next) {
      onOpenChange(true);
      return;
    }
    requestOwnerTransition({
      effect: 'close',
      affects: (record) => record.id === owner.id,
      proceed: () => {
        if (!busy && owner.record?.status !== 'unknown' && phase.kind !== 'edit') reset(requestedMode);
        onOpenChange(false);
      },
    });
  };

  // Only offered once the create is resolved (the owner no longer holds).
  const viewAgent = (canonicalId: string) => {
    if (slug) navigate(`/orgs/${slug}/agents/${encodeURIComponent(canonicalId)}`);
    reset(requestedMode);
    onOpenChange(false);
  };
  const refreshReads = () => {
    void agentsQuery.refetch();
    void teamsQuery.refetch();
    void qc.refetchQueries({ queryKey: ['identities', slug ?? ''], exact: true });
  };
  const refreshUnavailable = agentsQuery.isError || teamsQuery.isError || identitiesQuery.isError;
  const ownerParams = owner.record ? ownerContextParams(owner.record, t) : { computer: '', org: slug ?? '' };

  const title =
    phase.kind === 'created'
      ? t(phase.mode === 'team' ? 'agents.create.success.teamTitle' : 'agents.create.success.agentTitle')
      : phase.kind === 'setupIncomplete'
        ? t('agents.create.setupIncomplete.title')
        : phase.kind === 'found'
          ? t('agents.create.found.title')
          : unknown
            ? t('agents.create.unknown.title')
            : t(mode === 'team' ? 'agents.create.titleTeam' : 'agents.create.titleAgent');

  const setupCommand = (id: string | null) => `happyranch init-agent --org ${slug ?? ''} ${id ?? ''}`.trim();

  return (
    <Dialog open={open && !orgChanged} onOpenChange={requestOpenChange}>
      <DialogContent closeLabel={t('common.close')}>
        <DialogHeader>
          <DialogTitle>{title}</DialogTitle>
          {editing && !unknown && (
            <DialogDescription>
              {t(mode === 'team' ? 'agents.create.teamLede' : 'agents.create.agentLede')}
            </DialogDescription>
          )}
        </DialogHeader>

        {phase.kind === 'created' ? (
          <div className="space-y-3 text-sm" data-create-state="created">
            <p role="status">
              {phase.mode === 'team'
                ? t('agents.create.success.teamBody', { team: phase.team, name: phase.name })
                : t('agents.create.success.agentBody', { team: phase.team, name: phase.name })}
            </p>
            {refreshUnavailable && (
              <div role="alert" className="border-feedback-warning/40 bg-feedback-warning/5 rounded-sm border p-3">
                <p>{t('agents.create.success.refreshUnavailable')}</p>
                <Button size="sm" variant="secondary" className="mt-2" onClick={refreshReads}>
                  {t('agents.create.success.refresh')}
                </Button>
              </div>
            )}
          </div>
        ) : phase.kind === 'setupIncomplete' ? (
          <div role="alert" data-create-state="setup-incomplete" className="border-feedback-warning/40 bg-feedback-warning/5 space-y-2 rounded-sm border p-3 text-sm break-words">
            <p>{t('agents.create.setupIncomplete.body', { name: phase.name, team: phase.team })}</p>
            {phase.canonicalId && (
              <>
                <p className="text-text-secondary text-xs">{t('agents.create.setupIncomplete.recovery')}</p>
                <code className="bg-surface-sunken block rounded-sm p-2 text-xs break-all">{setupCommand(phase.canonicalId)}</code>
              </>
            )}
          </div>
        ) : phase.kind === 'found' ? (
          <div role="status" data-create-state="found" className="space-y-2 text-sm break-words">
            <p>{t('agents.create.found.body', { name: phase.name, id: phase.canonicalId })}</p>
            <p className="text-text-secondary text-xs">{t('agents.create.setupIncomplete.recovery')}</p>
            <code className="bg-surface-sunken block rounded-sm p-2 text-xs break-all">{setupCommand(phase.canonicalId)}</code>
          </div>
        ) : (
          <form id="create-agent-form" className="space-y-4" onSubmit={onSubmit} noValidate>
            {unknown && (
              <div role="alert" data-create-state="unknown" className="border-feedback-warning/40 bg-feedback-warning/5 text-text-primary rounded-sm border p-3 text-sm break-words">
                <p>{t('agents.create.unknown.body', { name: sent.current?.body.display_name ?? name, computer: ownerParams.computer, org: ownerParams.org })}</p>
                {finding?.kind === 'nameOnly' && (
                  <p className="mt-2">{t('agents.create.unknown.nameOnly', { id: finding.canonicalId ?? '' })}</p>
                )}
                <div className="mt-2 flex flex-wrap gap-2">
                  <Button
                    type="button"
                    size="sm"
                    variant="secondary"
                    disabled={owner.checking}
                    onClick={() => void owner.check().catch(() => undefined)}
                  >
                    {t(owner.checking ? 'owners.save.checking' : 'owners.save.checkResult')}
                  </Button>
                </div>
                {finding?.kind === 'incomplete' && (
                  <p className="mt-2">{t('agents.create.unknown.incomplete')}</p>
                )}
                {owner.checkFailed && !owner.checking && finding?.kind !== 'nameOnly' && finding?.kind !== 'incomplete' && (
                  <p className="text-feedback-danger mt-1 text-xs">{t('owners.save.checkFailed')}</p>
                )}
              </div>
            )}
            {!unknown && finding?.kind === 'absent' && (
              <p role="status" className="text-text-secondary text-sm">{t('agents.create.unknown.absent')}</p>
            )}
            {busy && (
              <p role="status" className="text-text-secondary text-sm">
                {t('agents.create.pending', { name: sent.current?.body.display_name ?? name, computer: ownerParams.computer, org: ownerParams.org })}
              </p>
            )}

            <fieldset disabled={frozen} className="m-0 min-w-0 space-y-4 border-0 p-0">
              <div>
                <Label htmlFor="agent-name">
                  {t(role === 'manager' ? 'agents.create.managerName' : 'agents.add.name')}
                </Label>
                <Input
                  id="agent-name"
                  value={name}
                  onChange={(e) => onNameChange(e.target.value)}
                  placeholder={t('agents.add.namePlaceholder')}
                  autoFocus
                  aria-invalid={errorFor('name', nameMessage) !== null}
                  aria-describedby="agent-name-hint agent-name-error"
                />
                <p id="agent-name-hint" className="text-text-secondary text-xs">{t('agents.add.nameHint')}</p>
                <p id="agent-name-error" className="text-tier-red text-xs" aria-live="polite">
                  {errorFor('name', nameMessage)}
                </p>
              </div>

              {mode === 'team' ? (
                <p className="text-text-secondary text-sm" data-testid="create-role-fixed">
                  {t('agents.create.roleFixed')}
                </p>
              ) : (
                <fieldset>
                  <legend className="text-sm font-medium">{t('agents.add.role')}</legend>
                  <div className="mt-1 flex flex-col gap-1 sm:flex-row sm:gap-4">
                    <label className="inline-flex items-start gap-1.5 text-sm">
                      <input
                        type="radio"
                        name="role"
                        value="worker"
                        className="mt-1"
                        checked={role === 'worker'}
                        onChange={() => onRoleChange('worker')}
                      />
                      <span>
                        {t('agents.role.worker')}
                        <span className="text-text-secondary block text-xs">{t('agents.create.workerHint')}</span>
                      </span>
                    </label>
                    <label className="inline-flex items-start gap-1.5 text-sm">
                      <input
                        type="radio"
                        name="role"
                        value="manager"
                        className="mt-1"
                        checked={role === 'manager'}
                        onChange={() => onRoleChange('manager')}
                      />
                      <span>
                        {t('agents.role.manager')}
                        <span className="text-text-secondary block text-xs">{t('agents.create.managerHint')}</span>
                      </span>
                    </label>
                  </div>
                </fieldset>
              )}

              {role === 'worker' ? (
                teamsQuery.isLoading ? (
                  <p className="text-text-secondary text-sm" role="status">{t('agents.add.teamsLoading')}</p>
                ) : teamsQuery.isError ? (
                  <div className="space-y-2" role="alert">
                    <p className="text-text-secondary text-sm">{t('agents.add.teamsError')}</p>
                    <Button type="button" variant="outline" size="sm" onClick={() => void teamsQuery.refetch()}>
                      {t('common.retry')}
                    </Button>
                  </div>
                ) : teams.length === 0 ? (
                  <div className="space-y-2" data-create-state="no-teams">
                    <p className="text-text-secondary text-sm">{t('agents.create.noTeams')}</p>
                    <Button type="button" variant="outline" size="sm" onClick={() => reset('team')}>
                      {t('agents.page.createTeam')}
                    </Button>
                  </div>
                ) : (
                  <div>
                    <Label htmlFor="agent-team">{t('agents.add.team')}</Label>
                    <select
                      id="agent-team"
                      value={team}
                      onChange={(e) => {
                        setTeam(e.target.value);
                        clearFieldError('team');
                      }}
                      aria-invalid={errorFor('team', teamMessage) !== null}
                      aria-describedby="agent-team-error"
                      className={SELECT_CLASS}
                    >
                      <option value="">{t('agents.add.selectTeam')}</option>
                      {teamStale && <option value={team} disabled>{team}</option>}
                      {teams.map((entry) => (
                        <option key={entry.name} value={entry.name}>
                          {entry.name}{entry.manager_kind === 'human' ? ` · ${t('agents.team.founderManaged')}` : ''}
                        </option>
                      ))}
                    </select>
                    <p id="agent-team-error" className="text-tier-red text-xs" aria-live="polite">
                      {errorFor('team', teamMessage)}
                    </p>
                  </div>
                )
              ) : (
                <div>
                  <Label htmlFor="agent-new-team">{t('agents.add.newTeam')}</Label>
                  <Input
                    id="agent-new-team"
                    value={newTeam}
                    onChange={(e) => onNewTeamChange(e.target.value)}
                    placeholder={t('agents.add.newTeamPlaceholder')}
                    aria-invalid={errorFor('newTeam', newTeamMessage) !== null}
                    aria-describedby="agent-new-team-hint agent-new-team-error"
                  />
                  <p id="agent-new-team-hint" className="text-text-secondary text-xs">
                    <span className="block">{t('agents.create.newTeamHint')}</span>
                    <span className="block">{t(teamEdited ? 'agents.create.teamEdited' : 'agents.create.teamSuggested')}</span>
                  </p>
                  <p id="agent-new-team-error" className="text-tier-red text-xs" aria-live="polite">
                    {errorFor('newTeam', newTeamMessage)}
                  </p>
                </div>
              )}

              <div>
                <Label htmlFor="agent-executor">{t('agents.executor.label')}</Label>
                {executorOptions.state === 'loading' ? (
                  <p className="text-text-secondary text-sm" role="status">{t('agents.executor.loading')}</p>
                ) : executorOptions.state === 'error' ? (
                  <div className="space-y-2" role="alert">
                    <p className="text-tier-red text-sm">{t('agents.add.executorError')}</p>
                    <Button type="button" variant="outline" size="sm" onClick={executorOptions.retry}>
                      {t('common.retry')}
                    </Button>
                  </div>
                ) : executorOptions.state === 'empty' ? (
                  <div className="space-y-2">
                    <p className="text-text-secondary text-sm">
                      {t('agents.add.noExecutors')}
                      {executorOptions.unavailable.length > 0 && (
                        <>
                          {' '}
                          {t('agents.add.notLaunchable', {
                            names: executorOptions.unavailable.map((e) => e.name).join(', '),
                          })}
                        </>
                      )}
                    </p>
                    {slug && (
                      <p className="text-text-secondary text-xs">
                        {render('agents.add.registerVia', {
                          link: (
                            <Link to={`/orgs/${slug}/settings/executors`} className="text-accent-text underline">
                              {t('agents.executor.settingsLink')}
                            </Link>
                          ),
                        })}
                      </p>
                    )}
                    <Button type="button" variant="outline" size="sm" onClick={executorOptions.retry}>
                      {t('common.retry')}
                    </Button>
                  </div>
                ) : (
                  <select
                    id="agent-executor"
                    value={executor}
                    onChange={(e) => {
                      setExecutor(e.target.value);
                      setExecutorPicked(true);
                      clearFieldError('executor');
                    }}
                    aria-invalid={executorStale || fieldError?.field === 'executor'}
                    aria-describedby="agent-executor-error"
                    className={SELECT_CLASS}
                  >
                    {executorStale && <option value={executor} disabled>{executor}</option>}
                    {executorOptions.selectable.map((opt) => (
                      <option key={opt.name} value={opt.name}>
                        {opt.kind === 'custom' ? t('agents.executor.customOption', { name: opt.name }) : opt.name}
                      </option>
                    ))}
                    {executorOptions.unavailable.length > 0 && (
                      <>
                        <option disabled>{t('agents.executor.unavailableHeader')}</option>
                        {executorOptions.unavailable.map((opt) => (
                          <option key={opt.name} value={opt.name} disabled>
                            {opt.kind === 'custom'
                              ? t('agents.add.customUnavailableOption', { name: opt.name })
                              : t('agents.add.notRegisteredOption', { name: opt.name })}
                          </option>
                        ))}
                      </>
                    )}
                  </select>
                )}
                <p id="agent-executor-error" className="text-tier-red text-xs" aria-live="polite">
                  {errorFor('executor', executorStale ? 'agents.create.error.executorStale' : null)}
                </p>
                {slug && executorOptions.state === 'ready' && executorOptions.unavailable.length > 0 && (
                  <p className="text-text-secondary mt-1 text-xs">
                    {t('agents.executor.needRegister')}{' '}
                    <Link to={`/orgs/${slug}/settings/executors`} className="text-accent-text underline">
                      {t('agents.executor.settingsLink')}
                    </Link>
                  </p>
                )}
              </div>

              <div>
                <Label htmlFor="agent-description">{t('agents.field.description')}</Label>
                <Input
                  id="agent-description"
                  value={description}
                  onChange={(e) => {
                    setDescription(e.target.value);
                    clearFieldError('description');
                  }}
                  aria-invalid={show('description', !descriptionOk)}
                  aria-describedby="agent-description-error"
                />
                <p id="agent-description-error" className="text-tier-red text-xs" aria-live="polite">
                  {show('description', !descriptionOk) ? t('agents.create.error.descriptionRequired') : null}
                </p>
              </div>

              <div>
                <Label htmlFor="agent-system-prompt">{t('agents.field.systemPrompt')}</Label>
                <Textarea
                  id="agent-system-prompt"
                  value={systemPrompt}
                  onChange={(e) => {
                    setSystemPrompt(e.target.value);
                    clearFieldError('systemPrompt');
                  }}
                  rows={6}
                  aria-invalid={show('systemPrompt', !promptOk)}
                  aria-describedby="agent-system-prompt-error"
                />
                <p id="agent-system-prompt-error" className="text-tier-red text-xs" aria-live="polite">
                  {show('systemPrompt', !promptOk) ? t('agents.create.error.promptRequired') : null}
                </p>
              </div>
            </fieldset>

            {refusal.host !== null ? (
              <RemoteReadOnlyNote host={refusal.host || t('remotePolicy.theHost')} variant="refused" className="" />
            ) : remoteHost ? (
              <RemoteReadOnlyNote host={remoteHost} className="" />
            ) : formError && (
              <p role="alert" className="text-tier-red text-sm">
                {renderAgentError(formError, t, params)}
              </p>
            )}
          </form>
        )}

        <DialogFooter className="flex-wrap gap-2">
          {phase.kind === 'created' ? (
            <>
              {phase.mode === 'team' && (
                <Button
                  variant="secondary"
                  disabled={!teams.some((entry) => entry.name === phase.team)}
                  onClick={() => reset('agent', phase.team)}
                >
                  {t('agents.create.success.addToTeam', { team: phase.team })}
                </Button>
              )}
              <Button onClick={() => requestOpenChange(false)}>{t('agents.create.done')}</Button>
            </>
          ) : phase.kind === 'setupIncomplete' || phase.kind === 'found' ? (
            <>
              {phase.canonicalId && (
                <Button variant="secondary" onClick={() => viewAgent(phase.canonicalId!)}>
                  {t('agents.create.viewAgent')}
                </Button>
              )}
              <Button onClick={() => requestOpenChange(false)}>{t('agents.create.done')}</Button>
            </>
          ) : (
            <>
              <Button type="button" variant="ghost" onClick={() => requestOpenChange(false)}>
                {t('common.cancel')}
              </Button>
              {!unknown && (
                <Button type="submit" form="create-agent-form" disabled={!canSubmit}>
                  {busy
                    ? t('agents.add.creating')
                    : t(mode === 'team' ? 'agents.page.createTeam' : 'agents.page.createAgent')}
                </Button>
              )}
            </>
          )}
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
