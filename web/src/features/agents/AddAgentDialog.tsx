/**
 * AddAgentDialog — the ONE shared Founder creation surface, opened in two
 * modes (THR-402; PRD docs/product/prds/2026-11-26-team-and-agent-creation.md;
 * layout from Claude Design v1.1, Founder THR-402 seq33):
 *
 *   - Create team (`mode="team"`): the outcome is fixed ("A new team starts
 *     with one new manager"); the form asks for the manager name and the new
 *     team name. Submits one manager enrollment with `new_team`.
 *   - Create agent (`mode="agent"`): "What will this agent do?" — Join an
 *     existing team · Worker (default) or Lead a new team · Manager. A worker
 *     picks a team explicitly from cards that name each team's manager
 *     (Founder-managed Default included; nothing preselected unless a
 *     confirmed team context opened the form). Only the visible branch's team
 *     field is submitted.
 *
 * Surface: a right-anchored drawer (desktop) / full-height sheet (phone) with
 * a context header (Computer and Organization chips), three numbered groups
 * (Team and role → Agent responsibilities → Runtime), a live review rail with
 * Edit on every row (section 4 at the end of the scroll on phone), and a
 * footer status line + Cancel + the outcome-named action. Disclosures (the
 * executor list, the instructions example, review instructions, submitted
 * details, Finish setup) are React state, so a locale change keeps them and
 * keeps focus where it is (N4); Edit and first-error focus open a closed
 * disclosure before focusing (R6).
 *
 * Rules live in `./createAgentForm` (pure; `web/scripts/create-agent-form.test.mjs`):
 * - field refusals are remembered with the refused value: any change of that
 *   value (typing, Use suggestion, a name-driven suggestion update) drops the
 *   refusal and its banner (N1); a refusal for the branch that is not shown
 *   is parked, not blocking, and returns with its branch (R1);
 * - Create is aria-disabled with a footer reason while executors are loading,
 *   failed to load or are confirmed none (N3), on a remote Host, or while the
 *   desktop target is not ready; Retry/Recheck are reads and never submit.
 *
 * Submission (A8/A9) goes through the existing config-owner machinery
 * (`useConfigOwner`, page `agents.create`): single-flight per owner, the
 * submitted body frozen, never re-sent on a 401 (`replayOnUnauthorized:
 * false` in `lib/api/agents#createAgent`). Outcomes:
 *   - success → the surface closes onto the returned canonical detail and
 *     `onCreated` lets the Agents page show its completion panel;
 *   - a confirmed refusal (4xx) → the matching field explains it, the rest
 *     stays, correct and resubmit;
 *   - `agent_setup_incomplete` → result panel: what exists / what remains,
 *     View agent and Finish setup (`happyranch init-agent --org <original>`);
 *     without a returned ID: Check result on the original org + support
 *     details, no command, no View agent, no second Create;
 *   - a typed pre-forward refusal → "Not sent" naming its existing cause and
 *     next step; the draft stays; only an explicit resubmit sends it;
 *   - no trustworthy answer → "Creation result unknown" result panel with
 *     Check result, which re-reads the ORIGINAL org (`lib/freshReads`).
 * Closing while a sent create is pending/unknown only hides the surface: the
 * owner keeps holding navigation and org/target changes, and the Agents page
 * shows a held-action chip that reopens the same result. On a remote Host
 * the form stays visible but read-only. In the desktop app, Open Connection
 * opens a drawer-owned menu: the current computer just closes it; another
 * computer asks the owner gate (Keep editing / Discard) and then hands off to
 * the existing Connection dialog, which owns every target change.
 */
import { useEffect, useMemo, useRef, useState, type FormEvent, type KeyboardEvent, type ReactNode } from 'react';
import { flushSync } from 'react-dom';
import { Link, useNavigate, useParams } from 'react-router-dom';
import { useQueryClient } from '@tanstack/react-query';
import { Loader2 } from 'lucide-react';
import { Button } from '@/design-system/primitives/Button';
import { Dialog, DialogContent, DialogDescription, DialogTitle } from '@/design-system/primitives/Dialog';
import { Input } from '@/design-system/primitives/Input';
import { Textarea } from '@/design-system/primitives/Textarea';
import { useCreateAgent } from '@/hooks/agents';
import { useIdentityPresentation } from '@/hooks/identities';
import { useTeamsList } from '@/hooks/teams';
import { useExecutorOptions } from '@/hooks/executor-options';
import { useTranslation } from '@/hooks/i18n';
import { useRetryNativeRecovery } from '@/hooks/nativeRecovery';
import type { MessageKey } from '@/lib/i18n';
import type { AgentSummary, IdentityList } from '@/lib/api/types';
import { useNativeAppStatus } from '@/lib/native/hooks';
import { refreshAppStatus } from '@/lib/native/target';
import { REMOTE_OPS, useRemoteReadOnly, useRemoteRefusal } from '@/lib/remotePolicy';
import { RemoteReadOnlyNote } from '@/shared/remotePolicy/RemoteReadOnlyNote';
import { useOpenConnection } from '@/shared/connection/openConnection';
import { ownerContextParams, useConfigOwner, useOwnerGate } from '@/shared/owners';
import { freshReads } from '@/lib/freshReads';
import { classifyAgentError, renderAgentError, type AgentErrorView } from './strings';
import {
  BRANCH_FIELDS,
  agentNameProblem,
  buildCreateBody,
  classifyCreateError,
  executorBlocker,
  fieldForCode,
  findCreatedAgent,
  newTeamProblem,
  notSentCopy,
  refusalStatus,
  suggestTeamName,
  type CheckFinding,
  type CreateBody,
  type CreateField,
  type CreateMode,
  type CreateRole,
  type FieldRefusal,
  type NotSentReason,
} from './createAgentForm';
import {
  FieldError,
  FieldLabel,
  FixedRelationship,
  GroupLegend,
  Help,
  Note,
  OptionCard,
  RelationshipLine,
  ResultIcon,
  ReviewRow,
} from './createParts';

/** A confirmed create, reported to the Agents page for its completion panel. */
export interface CreatedInfo {
  mode: CreateMode;
  canonicalId: string;
  name: string;
  team: string;
}

interface Props {
  open: boolean;
  /** Which action opened the form. Ignored while a sent create is unresolved. */
  mode: CreateMode;
  /** A confirmed team context (the team-success follow-on) preselects that team. */
  preselectTeam?: string;
  onOpenChange: (open: boolean) => void;
  onCreated?: (created: CreatedInfo) => void;
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
  | { kind: 'setupIncomplete'; canonicalId: string | null; name: string; team: string }
  | { kind: 'found'; canonicalId: string; name: string };

interface Sent {
  body: CreateBody;
  slug: string;
  mode: CreateMode;
  /** Localized computer label at send time (the original custody). */
  computer: string;
  time: string;
}

type TargetBlock = 'remote' | 'no_target' | 'target_starting' | 'unreachable' | 'incompatible';

const TARGET_COPY: Record<Exclude<TargetBlock, 'remote'>, { title: MessageKey; body: MessageKey; recheck: boolean; connection: boolean }> = {
  no_target: { title: 'agents.create.target.noTarget.title', body: 'agents.create.target.noTarget.body', recheck: false, connection: true },
  target_starting: { title: 'agents.create.target.starting.title', body: 'agents.create.target.starting.body', recheck: true, connection: false },
  unreachable: { title: 'agents.create.target.unreachable.title', body: 'agents.create.target.unreachable.body', recheck: true, connection: true },
  incompatible: { title: 'agents.create.target.incompatible.title', body: 'agents.create.target.incompatible.body', recheck: true, connection: false },
};

const INPUT_CLASS = 'bg-surface-canvas min-h-10.5';

const q = <T extends Element>(selector: string): T | null => document.querySelector<T>(selector);

export function AddAgentDialog({ open, mode: requestedMode, preselectTeam = '', onOpenChange, onCreated }: Props): JSX.Element {
  const { slug } = useParams<{ slug: string }>();
  const navigate = useNavigate();
  const qc = useQueryClient();
  const { t, locale, render: renderT } = useTranslation();
  const teamsQuery = useTeamsList();
  const identityPresentation = useIdentityPresentation();
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
  const [attempted, setAttempted] = useState(false);
  // Server refusals remembered with the value they refused (R1/N1).
  const [refusals, setRefusals] = useState<readonly FieldRefusal<AgentErrorView>[]>([]);
  const [formError, setFormError] = useState<AgentErrorView | null>(null);
  const [notSent, setNotSent] = useState<NotSentReason | null>(null);
  const [phase, setPhase] = useState<Phase>({ kind: 'edit' });
  const [finding, setFinding] = useState<CheckFinding | null>(null);
  const [noIdCheck, setNoIdCheck] = useState<'idle' | 'checking' | 'unresolved'>('idle');
  // Disclosures: React state, so a locale change keeps them (N4).
  const [execListOpen, setExecListOpen] = useState(false);
  const [exampleOpen, setExampleOpen] = useState(false);
  const [fullInstructions, setFullInstructions] = useState(false);
  const [submittedOpen, setSubmittedOpen] = useState(false);
  const [finishOpen, setFinishOpen] = useState(true);
  const [copied, setCopied] = useState(false);
  const [menuOpen, setMenuOpen] = useState(false);
  const menuTrigger = useRef<HTMLButtonElement | null>(null);
  const sent = useRef<Sent | null>(null);
  const latch = useRef(false);

  const create = useCreateAgent();
  const executorOptions = useExecutorOptions();
  const remoteHost = useRemoteReadOnly(REMOTE_OPS.createAgent);
  const refusal = useRemoteRefusal();
  const app = useNativeAppStatus();
  const openConnection = useOpenConnection();
  const retryRecovery = useRetryNativeRecovery();

  const reset = (next: CreateMode, preselect = ''): void => {
    setMode(next);
    setRole(next === 'team' ? 'manager' : 'worker');
    setName('');
    setTeam(preselect);
    setNewTeam('');
    setTeamEdited(false);
    setExecutor('');
    setExecutorPicked(false);
    setDescription('');
    setSystemPrompt('');
    setTouched(new Set());
    setAttempted(false);
    setRefusals([]);
    setFormError(null);
    setNotSent(null);
    setPhase({ kind: 'edit' });
    setFinding(null);
    setNoIdCheck('idle');
    setExecListOpen(false);
    setExampleOpen(false);
    setFullInstructions(false);
    setSubmittedOpen(false);
    setFinishOpen(true);
    setMenuOpen(false);
    sent.current = null;
    refusal.clear();
  };

  const editing = phase.kind === 'edit';
  const dirty =
    editing &&
    (name !== '' || (team !== '' && team !== preselectTeam) || description !== '' || systemPrompt !== '' || executorPicked ||
      (teamEdited && newTeam !== ''));
  const owner = useConfigOwner({
    page: 'agents.create',
    subject: sent.current?.body.display_name ?? (name || null),
    org: slug ?? null,
    dirty,
    draft: { mode, name, role, team, newTeam, executor, description, systemPrompt },
    onDiscard: () => reset(mode, preselectTeam),
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

  // The opening action picks the mode, unless a sent create is unresolved or a
  // created-without-ID result still needs its Check (both reopen as they were).
  const keepOnReopen = busy || unknown || (phase.kind === 'setupIncomplete' && phase.canonicalId === null);
  const wasOpen = useRef(open);
  useEffect(() => {
    if (open && !wasOpen.current && !keepOnReopen) reset(requestedMode, preselectTeam);
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

  // ---- context: computer · org (the submitted custody once sent) ----
  const thisComputer = t('owners.computer.thisComputer');
  const liveComputer =
    remoteHost ?? (app === null ? thisComputer : app.target === null ? t('agents.create.ctx.noComputer') : app.target.kind === 'remote' ? app.target.name : thisComputer);
  const ownerParams = owner.record ? ownerContextParams(owner.record, t) : null;
  const shownComputer = frozen ? (sent.current?.computer ?? String(ownerParams?.computer ?? liveComputer)) : liveComputer;
  const shownOrg = frozen ? (sent.current?.slug ?? slug ?? '') : (slug ?? '');
  const targetBlock: TargetBlock | null =
    remoteHost !== null
      ? 'remote'
      : app && (app.state === 'no_target' || app.state === 'target_starting' || app.state === 'unreachable' || app.state === 'incompatible')
        ? app.state
        : null;
  const execBlock = executorBlocker(executorOptions.state);

  // ---- values, refusals and validation ----
  const values: Record<CreateField, string> = { name, team, newTeam, executor, description, systemPrompt };
  const activeRefusals = refusals.filter((r) => refusalStatus(r, role, values) === 'active');
  const parkedNewTeam = refusals.find((r) => r.field === 'newTeam' && refusalStatus(r, role, values) === 'parked') ?? null;
  // N1: a refusal whose field value changed is dropped (never resurrected).
  useEffect(() => {
    if (refusals.some((r) => refusalStatus(r, role, values) === 'stale')) {
      setRefusals((prev) => prev.filter((r) => refusalStatus(r, role, values) !== 'stale'));
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [refusals, role, name, team, newTeam, executor, description, systemPrompt]);

  const touch = (field: CreateField) =>
    setTouched((prev) => (prev.has(field) ? prev : new Set(prev).add(field)));
  // Any edit clears the form-wide answers (a not-sent or other refusal banner);
  // field refusals drop by value comparison above.
  const edited = (field: CreateField) => {
    touch(field);
    setFormError(null);
    setNotSent(null);
    setFinding((prev) => (prev?.kind === 'absent' ? null : prev));
    refusal.clear();
  };

  const onNameChange = (next: string) => {
    setName(next);
    edited('name');
    if (role === 'manager' && !teamEdited) setNewTeam(suggestTeamName(next));
  };
  const onNewTeamChange = (next: string) => {
    setNewTeam(next);
    setTeamEdited(true);
    edited('newTeam');
  };
  const useSuggestion = () => {
    setNewTeam(suggestTeamName(name));
    setTeamEdited(false);
    edited('newTeam');
    requestAnimationFrame(() => document.getElementById(FIELD_IDS.newTeam)?.focus());
  };
  const onRoleChange = (next: CreateRole) => {
    setRole(next);
    setFormError(null);
    setNotSent(null);
    // The suggestion only fills an untouched team name.
    if (next === 'manager' && !teamEdited) setNewTeam(suggestTeamName(name));
  };

  const nameProblem = agentNameProblem(name);
  const newTeamIssue = newTeamProblem(newTeam, teamNames);
  const teamStale = team !== '' && teamNames !== null && !teamNames.includes(team);
  const executorStale = executorPicked && executorOptions.state !== 'loading' && !selectableNames.has(executor);
  const shown = (field: CreateField) => attempted || touched.has(field);

  const localError = (field: CreateField): MessageKey | null => {
    switch (field) {
      case 'name':
        if (nameProblem === 'invalid') return 'agents.create.nameHelp';
        return nameProblem === 'required' && shown('name') ? 'agents.create.error.nameRequired' : null;
      case 'team':
        if (teamsQuery.isLoading) return attempted ? 'agents.create.error.teamsLoading' : null;
        if (teamsQuery.isError) return attempted ? 'agents.create.error.teamsError' : null;
        if (teams.length === 0) return attempted ? 'agents.create.error.teamsEmpty' : null;
        if (teamStale) return 'agents.create.error.teamStale';
        return team === '' && shown('team') ? 'agents.create.error.teamRequired' : null;
      case 'newTeam':
        if (newTeamIssue === 'reserved') return 'agents.create.error.teamReserved';
        if (newTeamIssue === 'invalid') return 'agents.create.teamKeyHelp';
        if (newTeamIssue === 'exists') return 'agents.error.teamExists';
        return newTeamIssue === 'required' && shown('newTeam') ? 'agents.create.error.newTeamRequired' : null;
      case 'executor':
        if (executorOptions.state !== 'ready') return null; // a blocker with its own footer reason (N3)
        if (executorStale) return 'agents.create.error.executorStale';
        return !selectableNames.has(executor) && shown('executor') ? 'agents.create.error.executorRequired' : null;
      case 'description':
        return description.trim() === '' && shown('description') ? 'agents.create.error.descriptionRequired' : null;
      case 'systemPrompt':
        return systemPrompt.trim() === '' && shown('systemPrompt') ? 'agents.create.error.instructionsRequired' : null;
    }
  };
  // Whether a field blocks a send (independent of whether its message shows yet).
  const fieldInvalid = (field: CreateField): boolean => {
    if (activeRefusals.some((r) => r.field === field)) return true;
    switch (field) {
      case 'name': return nameProblem !== null;
      case 'team': return !teamsReady || teams.length === 0 || team === '' || teamStale;
      case 'newTeam': return newTeamIssue !== null;
      case 'executor': return executorOptions.state === 'ready' && !selectableNames.has(executor);
      case 'description': return description.trim() === '';
      case 'systemPrompt': return systemPrompt.trim() === '';
    }
  };
  const visibleFields = BRANCH_FIELDS[role];
  const invalidFields = visibleFields.filter(fieldInvalid);
  const params = { name, team, newTeam, executor };
  const sentParams = sent.current
    ? { name: sent.current.body.display_name, team: sent.current.body.team ?? '', newTeam: sent.current.body.new_team ?? '', executor: sent.current.body.executor }
    : params;
  const errorFor = (field: CreateField): string | null => {
    const server = activeRefusals.find((r) => r.field === field);
    if (server) return renderAgentError(server.view, t, sentParams);
    const local = localError(field);
    return local ? t(local, params) : null;
  };

  // ---- focus helpers (reveal any closed disclosure first: R6/N4) ----
  const focusTarget = (field: CreateField | 'role'): HTMLElement | null => {
    if (field === 'role') return q<HTMLInputElement>('input[name="create-role"]:checked');
    if (field === 'team') {
      return q<HTMLElement>('input[name="create-team"]:checked:not(:disabled)') ??
        q<HTMLElement>('input[name="create-team"]:not(:disabled)') ?? q<HTMLElement>('[data-focus-team]');
    }
    if (field === 'executor') {
      if (executorOptions.state === 'ready' && executorOptions.selectable.length === 1 && !execListOpen && !executorStale) {
        flushSync(() => setExecListOpen(true));
      }
      return q<HTMLElement>('input[name="create-executor"]:checked:not(:disabled)') ??
        q<HTMLElement>('input[name="create-executor"]:not(:disabled)') ?? q<HTMLElement>('[data-focus-executor]');
    }
    return document.getElementById(FIELD_IDS[field]);
  };
  const focusField = (field: CreateField | 'role') => {
    const el = focusTarget(field);
    if (!el) return;
    el.focus();
    el.scrollIntoView?.({ block: 'center' });
  };

  // ---- submission ----
  const blocked = frozen || targetBlock !== null || execBlock !== null || owner.saveRefused || !slug;

  const onSubmit = async (event?: FormEvent) => {
    event?.preventDefault();
    if (blocked) return;
    setAttempted(true);
    if (invalidFields.length > 0) {
      focusField(invalidFields[0]!);
      return;
    }
    // Single flight at the event boundary: a second click/Enter in the same
    // turn finds the latch (and owner.begin() refuses while one is in flight).
    if (latch.current || owner.isBusy() || !slug) return;
    const ticket = owner.begin();
    if (ticket === null) return;
    latch.current = true;
    const body = buildCreateBody({ name, role, team, newTeam, executor, description, systemPrompt });
    sent.current = {
      body,
      slug,
      mode,
      computer: liveComputer,
      time: new Intl.DateTimeFormat(locale, { hour: '2-digit', minute: '2-digit' }).format(new Date()),
    };
    refusal.clear();
    setRefusals([]);
    setFormError(null);
    setNotSent(null);
    setFinding(null);
    setMenuOpen(false);
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
      const info: CreatedInfo = {
        mode,
        canonicalId: created.canonical_id,
        name: created.addressable_name || body.display_name,
        team: created.team,
      };
      navigate(`/orgs/${slug}/agents/${encodeURIComponent(created.canonical_id)}`);
      reset(requestedMode);
      onOpenChange(false);
      onCreated?.(info);
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
      setNotSent(outcome.reason);
      showBanners();
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
        const sentValue: Record<CreateField, string> = {
          name: body.display_name,
          team: body.team ?? '',
          newTeam: body.new_team ?? '',
          executor: body.executor,
          description: body.description,
          systemPrompt: body.system_prompt,
        };
        setRefusals([{ field, value: sentValue[field], view }]);
        requestAnimationFrame(() => focusField(field));
      } else {
        setFormError(view);
        showBanners();
      }
    }
    // Otherwise the owner holds the unknown result (rendered below).
  };

  // Form-level answers (not sent, sign-in renewed, permission) sit at the top
  // of the scrolling body: bring them into view (they are role="alert").
  const bodyRef = useRef<HTMLDivElement | null>(null);
  const showBanners = () => requestAnimationFrame(() => bodyRef.current?.scrollTo({ top: 0 }));

  const onFormKeyDown = (event: KeyboardEvent<HTMLFormElement>) => {
    const target = event.target as HTMLElement;
    if (event.key !== 'Enter') return;
    if (event.metaKey || event.ctrlKey) {
      event.preventDefault();
      void onSubmit();
      return;
    }
    // Radios and the disclosure toggles never submit.
    if (target instanceof HTMLInputElement && target.type === 'radio') event.preventDefault();
  };

  // Close paths (close button, Escape, Cancel, Done). A sent create that is
  // pending/unknown is only hidden: its owner keeps holding and the Agents
  // page shows the held-action chip. Unsent edits get Keep editing / Discard.
  const { requestOwnerTransition } = useOwnerGate();
  const requestOpenChange = (next: boolean): void => {
    if (next) {
      onOpenChange(true);
      return;
    }
    setMenuOpen(false);
    if (busy || unknown || keepOnReopen) {
      onOpenChange(false);
      return;
    }
    requestOwnerTransition({
      effect: 'close',
      affects: (record) => record.id === owner.id,
      proceed: () => {
        if (phase.kind !== 'edit') reset(requestedMode);
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

  const runCheck = () => {
    setFinding(null);
    void owner.check().catch(() => undefined);
  };
  // Created without an ID: a read of the ORIGINAL org can find it (never a create).
  const checkNoId = async () => {
    const submitted = sent.current;
    if (!submitted || noIdCheck === 'checking') return;
    setNoIdCheck('checking');
    const reads = submitted.slug === slug
      ? await freshReads(qc, [['agents', submitted.slug], ['identities', submitted.slug]])
      : null;
    if (reads === null) {
      setNoIdCheck('unresolved');
      return;
    }
    const [agentsData, identitiesData] = reads as [{ agents: AgentSummary[] }, IdentityList];
    const result = findCreatedAgent(submitted.body, agentsData.agents, identitiesData);
    if (result.kind === 'matched') {
      setNoIdCheck('idle');
      setPhase((prev) => (prev.kind === 'setupIncomplete' ? { ...prev, canonicalId: result.canonicalId } : prev));
    } else setNoIdCheck('unresolved');
  };

  const recheck = () => {
    void refreshAppStatus();
    retryRecovery();
  };

  // ---- drawer-owned Connection menu (desktop only; N2) ----
  const currentKind: 'local' | 'remote' | null = app?.target?.kind ?? null;
  const menuItems: { key: 'local' | 'remote'; label: string }[] = [{ key: 'local', label: thisComputer }];
  if (app?.target?.kind === 'remote') menuItems.push({ key: 'remote', label: app.target.name });
  const closeMenu = () => {
    setMenuOpen(false);
    requestAnimationFrame(() => menuTrigger.current?.focus());
  };
  const pickComputer = (key: 'local' | 'remote') => {
    if (key === currentKind) {
      // N2: the current computer closes the menu, focus back on the drawer's trigger.
      closeMenu();
      return;
    }
    setMenuOpen(false);
    requestOwnerTransition({
      effect: 'openConnection',
      affects: (record) => record.id === owner.id,
      proceed: () => {
        onOpenChange(false);
        openConnection?.(null);
      },
      cancel: () => requestAnimationFrame(() => menuTrigger.current?.focus()),
    });
  };
  const onMenuKeyDown = (event: KeyboardEvent<HTMLDivElement>) => {
    const items = Array.from(event.currentTarget.querySelectorAll<HTMLButtonElement>('[role="menuitemradio"]'));
    const index = items.indexOf(document.activeElement as HTMLButtonElement);
    let next: number | null = null;
    if (event.key === 'ArrowDown') next = (index + 1) % items.length;
    else if (event.key === 'ArrowUp') next = (index - 1 + items.length) % items.length;
    else if (event.key === 'Home') next = 0;
    else if (event.key === 'End') next = items.length - 1;
    else if (event.key === 'Escape' || event.key === 'Tab') {
      event.preventDefault();
      event.stopPropagation();
      closeMenu();
      return;
    }
    if (next !== null) {
      event.preventDefault();
      items[next]?.focus();
    }
  };
  useEffect(() => {
    if (!menuOpen) return;
    // Focus starts on the current computer (the checked item), else the first.
    requestAnimationFrame(() =>
      (q<HTMLButtonElement>('[data-create-menu] [aria-checked="true"]') ??
        q<HTMLButtonElement>('[data-create-menu] [role="menuitemradio"]'))?.focus());
  }, [menuOpen]);
  const connectionButton = (key: string) =>
    openConnection ? (
      <span className="relative" key={key}>
        <Button
          type="button"
          size="sm"
          variant="outline"
          ref={menuTrigger}
          aria-haspopup="menu"
          aria-expanded={menuOpen}
          onClick={() => (menuOpen ? closeMenu() : setMenuOpen(true))}
        >
          {t('agents.create.openConnection')}
        </Button>
        {menuOpen && (
          <div
            role="menu"
            aria-label={t('agents.create.connection.menu')}
            data-create-menu
            onKeyDown={onMenuKeyDown}
            className="border-border-default bg-surface-canvas shadow-pasture-lg absolute top-full left-0 z-10 mt-1.5 w-72 rounded-md border p-2"
          >
            <p className="text-text-secondary px-1.5 pb-1 text-xs">{t('agents.create.connection.note')}</p>
            {dirty && <p className="text-attention-text px-1.5 pb-1 text-xs font-semibold">{t('agents.create.connection.discardNote')}</p>}
            {menuItems.map((item) => (
              <button
                key={item.key}
                type="button"
                role="menuitemradio"
                aria-checked={item.key === currentKind}
                tabIndex={-1}
                onClick={() => pickComputer(item.key)}
                className="hover:bg-surface-hover focus-visible:outline-accent-default text-text-primary flex w-full min-h-10 items-center gap-2 rounded-sm px-2 text-left text-sm focus-visible:outline-2"
              >
                <span aria-hidden="true" className={`h-1.5 w-1.5 shrink-0 rounded-full ${item.key === 'remote' ? 'bg-info' : 'bg-positive'}`} />
                <span className="min-w-0 break-words">{item.label}</span>
                {item.key === 'remote' && <span className="text-text-muted text-xs">· {t('agents.create.ctx.remote')}</span>}
              </button>
            ))}
          </div>
        )}
      </span>
    ) : null;

  // ---- derived presentation ----
  const outcomeRole: CreateRole = frozen && sent.current ? sent.current.body.role : role;
  const actionLabel = t(outcomeRole === 'manager' ? 'agents.page.createTeam' : 'agents.page.createAgent');
  const teamEntry = teams.find((entry) => entry.name === team) ?? null;
  const founderTeam = (entry: { manager_kind?: string; is_default?: boolean } | null) =>
    !!entry && (entry.manager_kind === 'human' || entry.is_default === true);
  const teamTitle = (entry: { name: string; is_default?: boolean }) =>
    entry.is_default ? t('agents.create.defaultTeam') : entry.name;
  const managerLabel = (entry: { manager: string | null }) => (entry.manager ? identityPresentation.name(entry.manager) : '');
  const pendingName = t('agents.create.review.pendingName');
  const pendingTeam = t('agents.create.review.pendingTeam');
  const bold = (value: string) => <b>{value}</b>;
  const outcomeSentence = (s: { role: CreateRole; name: string; team: string }): ReactNode =>
    s.role === 'manager'
      ? renderT('agents.create.review.outcomeTeam', { team: bold(s.team || pendingTeam), name: bold(s.name || pendingName) })
      : renderT('agents.create.review.outcomeWorker', {
        name: bold(s.name || pendingName),
        team: bold(s.team ? (teams.find((e) => e.name === s.team)?.is_default ? t('agents.create.defaultTeam') : s.team) : pendingTeam),
      });
  const liveOutcome = frozen && sent.current
    ? outcomeSentence({ role: sent.current.body.role, name: sent.current.body.display_name, team: sent.current.body.team ?? sent.current.body.new_team ?? '' })
    : outcomeSentence({ role, name, team: role === 'manager' ? newTeam : team });

  const errorCount = invalidFields.filter((field) => errorFor(field) !== null || attempted).length;
  const footerMessage: { text: ReactNode; tone: 'plain' | 'warn' | 'err' } = busy
    ? {
      text: (
        <span className="inline-flex items-center gap-2">
          <Loader2 aria-hidden="true" className="h-3.5 w-3.5 animate-spin motion-reduce:animate-none" />
          {t('agents.create.pending', { name: sent.current?.body.display_name ?? name, computer: shownComputer, org: shownOrg })}
        </span>
      ),
      tone: 'plain',
    }
    : targetBlock === 'remote'
      ? { text: t('agents.create.footer.remote', { host: remoteHost ?? '' }), tone: 'warn' }
      : targetBlock !== null
        ? { text: t(TARGET_COPY[targetBlock].title, { computer: liveComputer }), tone: 'warn' }
        : execBlock !== null
          ? { text: t(`agents.create.footer.${execBlock}` as MessageKey), tone: 'warn' }
          : attempted && errorCount > 0
            ? { text: errorCount === 1 ? t('agents.create.footer.fixOne') : t('agents.create.footer.fixMany', { count: errorCount }), tone: 'err' }
            : { text: <span className="text-text-muted hidden md:inline">{t('agents.create.footer.kbd')}</span>, tone: 'plain' };

  // Result panels replace the form (unknown / created-incomplete / found).
  const resultKind: 'unknown' | 'found' | 'setup' | 'setupNoId' | null =
    unknown ? 'unknown'
      : phase.kind === 'found' ? 'found'
        : phase.kind === 'setupIncomplete' ? (phase.canonicalId ? 'setup' : 'setupNoId')
          : null;
  const resultPrimary = useRef<HTMLButtonElement | null>(null);
  useEffect(() => {
    if (!open || resultKind === null) return;
    requestAnimationFrame(() => resultPrimary.current?.focus());
  }, [open, resultKind]);

  // Initial focus once the opening reset has rendered: the checked outcome
  // radio (Create agent), the manager name (Create team), or the primary
  // result action when a held result reopens (that effect is above).
  const initialFocus = useRef(false);
  useEffect(() => {
    if (!open || !initialFocus.current) return;
    if (!keepOnReopen && mode !== requestedMode) return;
    initialFocus.current = false;
    if (resultKind !== null) return;
    requestAnimationFrame(() =>
      (q<HTMLElement>('input[name="create-role"]:checked') ?? document.getElementById(FIELD_IDS.name))?.focus());
  });

  const setupCommand = (id: string) => `happyranch init-agent --org ${sent.current?.slug ?? slug ?? ''} ${id}`;
  const copyCommand = (command: string) => {
    void navigator.clipboard?.writeText(command).then(() => setCopied(true), () => undefined);
  };

  const submittedDetails = sent.current && (
    <details
      className="border-border-subtle mt-5 border-t pt-3"
      open={submittedOpen}
      onToggle={(event) => setSubmittedOpen(event.currentTarget.open)}
    >
      <summary className="text-accent-text cursor-pointer text-sm font-semibold">{t('agents.create.result.submitted')}</summary>
      <dl className="mt-2">
        <ReviewRow label={t('agents.create.review.role')} value={t(sent.current.body.role === 'manager' ? 'agents.role.manager' : 'agents.role.worker')} />
        <ReviewRow label={t('agents.create.review.team')} value={<span className="font-mono">{sent.current.body.team ?? sent.current.body.new_team}</span>} />
        <ReviewRow label={t('agents.create.review.name')} value={sent.current.body.display_name} />
        <ReviewRow label={t('agents.create.result.sentTo')} value={`${sent.current.computer} · ${sent.current.slug}`} />
        <ReviewRow label={t('agents.create.review.executor')} value={<span className="font-mono">{sent.current.body.executor}</span>} />
        <ReviewRow label={t('agents.create.review.description')} value={sent.current.body.description} />
        <ReviewRow label={t('agents.create.review.instructions')} value={<span className="whitespace-pre-wrap">{sent.current.body.system_prompt}</span>} />
      </dl>
    </details>
  );

  const finishSetup = (id: string) => (
    <details
      className="border-border-subtle mt-5 border-t pt-3"
      open={finishOpen}
      onToggle={(event) => setFinishOpen(event.currentTarget.open)}
    >
      <summary className="text-accent-text cursor-pointer text-sm font-semibold">{t('agents.create.result.finishSetup')}</summary>
      <div className="border-border-subtle bg-surface-raised mt-2 rounded-md border p-3.5 text-sm">
        <p className="text-text-secondary">{t('agents.create.result.finishBody', { computer: sent.current?.computer ?? shownComputer })}</p>
        <div className="bg-surface-sunken mt-2.5 flex flex-wrap items-center gap-2 rounded-sm p-2.5">
          <code className="min-w-0 flex-1 font-mono text-xs break-all">{setupCommand(id)}</code>
          <Button type="button" size="sm" variant="outline" onClick={() => copyCommand(setupCommand(id))}>
            {t(copied ? 'agents.create.result.copied' : 'agents.create.result.copy')}
          </Button>
        </div>
      </div>
    </details>
  );

  const existsList = (s: { role: CreateRole; name: string; team: string }) => (
    <ul className="mt-1 list-disc ps-5">
      {s.role === 'manager' ? (
        <>
          <li>{t('agents.create.result.existsTeam', { team: s.team })}</li>
          <li>{t('agents.create.result.existsManager', { name: s.name })}</li>
        </>
      ) : (
        <li>{t('agents.create.result.existsWorker', { name: s.name, team: s.team })}</li>
      )}
    </ul>
  );

  const resultPanel = (): ReactNode => {
    const s = sent.current;
    const computer = s?.computer ?? shownComputer;
    const org = s?.slug ?? shownOrg;
    const heading = (text: string) => (
      <h3 className="font-display text-text-primary mt-3 text-2xl leading-tight break-words">{text}</h3>
    );
    if (resultKind === 'unknown') {
      const checking = owner.checking;
      const variant = checking ? 'checking'
        : finding?.kind === 'nameOnly' ? 'nameOnly'
          : finding?.kind === 'incomplete' ? 'incomplete'
            : owner.checkFailed ? 'failed' : 'unknown';
      return (
        <div data-create-state={variant === 'unknown' || variant === 'checking' ? 'unknown' : `unknown-${variant}`} role="status">
          <ResultIcon kind="unknown" />
          {variant === 'nameOnly' ? (
            <>
              {heading(t('agents.create.result.nameTitle'))}
              <p className="text-text-primary mt-3 text-sm">{t('agents.create.result.nameBody', { name: s?.body.display_name ?? '' })}</p>
              {finding?.kind === 'nameOnly' && finding.canonicalId && (
                <div className="mt-3">
                  <Note kind="plain" title={t('agents.create.result.identity')}>
                    <span className="font-mono">{s?.body.display_name} · {finding.canonicalId}</span>
                  </Note>
                </div>
              )}
            </>
          ) : variant === 'incomplete' || variant === 'failed' ? (
            <>
              {heading(t('agents.create.result.unresolvedTitle'))}
              <p className="text-text-secondary mt-2 text-sm">
                {variant === 'incomplete' ? t('agents.create.result.unresolvedIncomplete') : t('agents.create.result.unresolvedFailed', { computer, org })}
              </p>
              <p className="text-text-primary mt-3 text-sm">{t('agents.create.result.unresolvedBody')}</p>
            </>
          ) : (
            <>
              {heading(t('agents.create.result.unknownTitle'))}
              <p className="text-text-secondary mt-2 text-sm">{t('agents.create.result.unknownSub')}</p>
              <p className="text-text-primary mt-3 text-sm [&_b]:font-semibold">
                {renderT('agents.create.result.unknownBody', { name: bold(s?.body.display_name ?? ''), computer: bold(computer), org: bold(org) })}
              </p>
            </>
          )}
          <div className="mt-4">
            <Button
              ref={resultPrimary}
              type="button"
              aria-disabled={checking || undefined}
              aria-busy={checking || undefined}
              onClick={() => !checking && runCheck()}
            >
              {checking && <Loader2 aria-hidden="true" className="animate-spin motion-reduce:animate-none" />}
              {t(checking ? 'agents.create.result.checking' : variant === 'unknown' ? 'owners.save.checkResult' : 'agents.create.result.checkAgain')}
            </Button>
          </div>
          {submittedDetails}
        </div>
      );
    }
    if (resultKind === 'found' && phase.kind === 'found') {
      return (
        <div data-create-state="found" role="status">
          <ResultIcon kind="ok" />
          {heading(t('agents.create.result.matchTitle'))}
          <p className="text-text-primary mt-3 text-sm">{t('agents.create.result.matchBody', { computer, org })}</p>
          <dl className="mt-3">
            <ReviewRow label={t('agents.create.review.name')} value={phase.name} />
            <ReviewRow label={t('agents.create.review.team')} value={<span className="font-mono">{s?.body.team ?? s?.body.new_team}</span>} />
            <ReviewRow label={t('agents.create.result.agentId')} value={<span className="font-mono">{phase.canonicalId}</span>} />
          </dl>
          <div className="mt-4">
            <Button ref={resultPrimary} type="button" onClick={() => viewAgent(phase.canonicalId)}>{t('agents.create.viewAgent')}</Button>
          </div>
          {finishSetup(phase.canonicalId)}
          {submittedDetails}
        </div>
      );
    }
    if ((resultKind === 'setup' || resultKind === 'setupNoId') && phase.kind === 'setupIncomplete') {
      const sRole: CreateRole = s?.body.role ?? 'worker';
      const summary = { role: sRole, name: phase.name, team: phase.team };
      const checking = noIdCheck === 'checking';
      return (
        <div data-create-state={resultKind === 'setup' ? 'setup-incomplete' : 'setup-incomplete-no-id'} role="alert">
          <ResultIcon kind="ok" />
          {heading(t('agents.create.result.setupTitle'))}
          {resultKind === 'setupNoId' && <p className="text-text-primary mt-3 text-sm">{t('agents.create.result.noIdBody', { computer, org })}</p>}
          <div className="mt-4 grid gap-3 sm:grid-cols-2">
            <Note kind="info" title={t('agents.create.result.exists')}>{existsList(summary)}</Note>
            <Note kind="warn" title={t('agents.create.result.remains')}>
              <ul className="mt-1 list-disc ps-5">
                {resultKind === 'setupNoId' && <li>{t('agents.create.result.remainsId')}</li>}
                <li>{t('agents.create.result.remainsWorkspace', { computer })}</li>
                {sRole === 'manager' && resultKind === 'setup' && <li>{t('agents.create.result.remainsWorkers')}</li>}
              </ul>
            </Note>
          </div>
          {resultKind === 'setupNoId' && noIdCheck === 'unresolved' && (
            <p role="status" className="text-text-secondary mt-3 text-sm">{t('agents.create.result.noIdUnresolved')}</p>
          )}
          <div className="mt-4">
            {resultKind === 'setup' && phase.canonicalId ? (
              <Button ref={resultPrimary} type="button" onClick={() => viewAgent(phase.canonicalId!)}>{t('agents.create.viewAgent')}</Button>
            ) : (
              <Button
                ref={resultPrimary}
                type="button"
                aria-disabled={checking || undefined}
                aria-busy={checking || undefined}
                onClick={() => void checkNoId()}
              >
                {checking && <Loader2 aria-hidden="true" className="animate-spin motion-reduce:animate-none" />}
                {t(checking ? 'agents.create.result.checking' : 'owners.save.checkResult')}
              </Button>
            )}
          </div>
          {resultKind === 'setup' && phase.canonicalId && finishSetup(phase.canonicalId)}
          {resultKind === 'setupNoId' && (
            <div className="mt-4">
              <Note kind="plain" title={t('agents.create.result.supportTitle')}>
                {t('agents.create.result.supportBody', { name: phase.name, computer, org, time: s?.time ?? '' })}
              </Note>
            </div>
          )}
          {submittedDetails}
        </div>
      );
    }
    return null;
  };

  // ---- banners above the form ----
  const banners: ReactNode[] = [];
  if (editing && !frozen) {
    if (refusal.host !== null) {
      banners.push(<RemoteReadOnlyNote key="refused" host={refusal.host || t('remotePolicy.theHost')} variant="refused" className="" />);
    } else if (targetBlock === 'remote') {
      banners.push(
        <Note key="remote" kind="warn" title={t('agents.create.footer.remote', { host: remoteHost ?? '' })} actions={connectionButton('conn')}>
          {t('agents.create.remote.body', { host: remoteHost ?? '' })}
        </Note>,
      );
    } else if (targetBlock !== null) {
      const copy = TARGET_COPY[targetBlock];
      banners.push(
        <Note
          key="target"
          kind="warn"
          title={t(copy.title, { computer: liveComputer })}
          actions={(copy.recheck || (copy.connection && openConnection)) && (
            <>
              {copy.recheck && <Button type="button" size="sm" variant="outline" onClick={recheck}>{t('agents.create.recheck')}</Button>}
              {copy.connection && connectionButton('conn')}
            </>
          )}
        >
          {t(copy.body)}
        </Note>,
      );
    }
    if (notSent) {
      const copy = notSentCopy(notSent);
      const computer = sent.current?.computer ?? liveComputer;
      banners.push(
        <Note
          key="notsent"
          kind="warn"
          role="alert"
          testId="create-not-sent"
          title={t('agents.create.notSent.title')}
          actions={targetBlock === null && (copy.recheck || (copy.connection && openConnection)) && (
            <>
              {copy.recheck && <Button type="button" size="sm" variant="outline" onClick={recheck}>{t('agents.create.recheck')}</Button>}
              {copy.connection && connectionButton('conn-notsent')}
            </>
          )}
        >
          {t(`agents.create.notSent.${copy.cause}` as MessageKey, { computer })}{' '}
          {t(`agents.create.notSent.${copy.next}` as MessageKey, { computer })}
        </Note>,
      );
    }
    if (activeRefusals.length > 0) {
      banners.push(<Note key="refused-field" kind="bad" role="alert" testId="create-refused" title={t('agents.create.banner.refused')} />);
    } else if (parkedNewTeam) {
      banners.push(
        <Note key="parked" kind="info" role="status" testId="create-refusal-parked" title={t('agents.create.banner.parked', { team: parkedNewTeam.value })} />,
      );
    }
    if (formError) {
      const auth = formError.kind === 'message' && formError.key === 'agents.create.error.authRenewed';
      banners.push(<Note key="form" kind={auth ? 'info' : 'bad'} role="alert" title={renderAgentError(formError, t, params)} />);
    }
    if (finding?.kind === 'absent') {
      banners.push(
        <Note key="absent" kind="info" role="alert" testId="create-absent" title={t('agents.create.absent.title')}>
          {t('agents.create.absent.body', { computer: sent.current?.computer ?? liveComputer, org: sent.current?.slug ?? slug ?? '' })}
        </Note>,
      );
    }
  }

  // ---- form groups ----
  const describedBy = (field: CreateField, help = true) =>
    [help ? `${FIELD_IDS[field]}-help` : null, `${FIELD_IDS[field]}-error`].filter(Boolean).join(' ');
  const required = t('agents.create.required');

  const nameField = (
    <div className="mt-4">
      <FieldLabel htmlFor={FIELD_IDS.name} required={required}>
        {t(role === 'manager' ? 'agents.create.managerName' : 'agents.create.agentName')}
      </FieldLabel>
      <Input
        id={FIELD_IDS.name}
        value={name}
        onChange={(e) => onNameChange(e.target.value)}
        autoComplete="off"
        spellCheck={false}
        className={INPUT_CLASS}
        aria-required="true"
        aria-invalid={errorFor('name') !== null}
        aria-describedby={describedBy('name')}
      />
      <Help id={`${FIELD_IDS.name}-help`}>{t('agents.create.nameHelp')}</Help>
      <FieldError id={`${FIELD_IDS.name}-error`} message={errorFor('name')} />
    </div>
  );

  const relationship = (): ReactNode => {
    const who = name || pendingName;
    if (role === 'worker') {
      if (!teamEntry) return null;
      return (
        <RelationshipLine>
          {founderTeam(teamEntry)
            ? renderT('agents.create.rel.workerFounder', { name: bold(who), team: bold(teamTitle(teamEntry)) })
            : renderT('agents.create.rel.worker', { name: bold(who), team: bold(teamEntry.name), manager: bold(managerLabel(teamEntry)) })}
        </RelationshipLine>
      );
    }
    if (!newTeam) return null;
    return <RelationshipLine>{renderT('agents.create.rel.manager', { name: bold(who), team: bold(newTeam) })}</RelationshipLine>;
  };

  const teamPicker = (
    <fieldset className="m-0 mt-4 min-w-0 border-0 p-0" aria-describedby={describedBy('team')}>
      <FieldLabel as="legend" required={required}>{t('agents.create.teamLabel')}</FieldLabel>
      {teamsQuery.isLoading ? (
        <div role="status" className="border-border-subtle bg-surface-raised rounded-md border p-3.5">
          <div className="bg-surface-hover mb-2.5 h-3 w-2/3 animate-pulse rounded-sm motion-reduce:animate-none" />
          <div className="bg-surface-hover mb-2.5 h-3 w-1/2 animate-pulse rounded-sm motion-reduce:animate-none" />
          <span className="text-text-muted text-xs">{t('agents.create.teamsLoading')}</span>
        </div>
      ) : teamsQuery.isError ? (
        <Note
          kind="warn"
          role="alert"
          title={t('agents.create.teamsError')}
          actions={<Button type="button" size="sm" variant="outline" data-focus-team onClick={() => void teamsQuery.refetch()}>{t('common.retry')}</Button>}
        />
      ) : teams.length === 0 ? (
        <div data-create-state="no-teams">
          <Note
            kind="info"
            title={t('agents.create.teamsEmpty.title', { org: slug ?? '' })}
            actions={mode === 'agent' && (
              <Button type="button" size="sm" data-focus-team onClick={() => { onRoleChange('manager'); requestAnimationFrame(() => focusField('name')); }}>
                {t('agents.create.teamsEmpty.switch')}
              </Button>
            )}
          >
            <p>{t('agents.create.teamsEmpty.body')}</p>
            {mode === 'agent' && <p className="text-text-muted mt-1 text-xs">{t('agents.create.teamsEmpty.note')}</p>}
          </Note>
        </div>
      ) : (
        <div className="grid gap-2.5">
          {teams.map((entry, i) => (
            <OptionCard
              key={entry.name}
              id={`${FIELD_IDS.team}-${i}`}
              name="create-team"
              value={entry.name}
              checked={team === entry.name}
              title={entry.is_default ? teamTitle(entry) : <span className="font-mono">{entry.name}</span>}
              detail={founderTeam(entry) ? t('agents.create.founderManaged') : entry.manager ? t('agents.create.managerOf', { manager: managerLabel(entry) }) : null}
              onChange={() => {
                setTeam(entry.name);
                edited('team');
              }}
            />
          ))}
          {teamStale && (
            <OptionCard
              id={`${FIELD_IDS.team}-stale`}
              name="create-team"
              value={team}
              checked
              disabled
              unavailable
              title={<span className="font-mono">{t('agents.create.teamUnavailable', { team })}</span>}
              onChange={() => undefined}
            />
          )}
        </div>
      )}
      <Help id={`${FIELD_IDS.team}-help`}>{t('agents.create.teamHelp')}</Help>
      <FieldError id={`${FIELD_IDS.team}-error`} message={errorFor('team')} />
      {relationship()}
    </fieldset>
  );

  const suggestion = suggestTeamName(name);
  const teamKeyField = (
    <div className="mt-4">
      <FieldLabel htmlFor={FIELD_IDS.newTeam} required={required}>{t('agents.add.newTeam')}</FieldLabel>
      <Input
        id={FIELD_IDS.newTeam}
        value={newTeam}
        onChange={(e) => onNewTeamChange(e.target.value)}
        autoComplete="off"
        spellCheck={false}
        className={`${INPUT_CLASS} font-mono`}
        aria-required="true"
        aria-invalid={errorFor('newTeam') !== null}
        aria-describedby={`${FIELD_IDS.newTeam}-hint ${describedBy('newTeam')}`}
      />
      <div id={`${FIELD_IDS.newTeam}-hint`} className="mt-1.5 flex flex-wrap items-center gap-2 text-xs">
        {teamEdited ? (
          <>
            <span className="text-accent-text font-semibold">{t('agents.create.keyManual')}</span>
            {suggestion && suggestion !== newTeam && (
              <button type="button" onClick={useSuggestion} className="text-accent-text font-semibold underline underline-offset-2">
                {t('agents.create.useSuggestion', { suggestion })}
              </button>
            )}
          </>
        ) : newTeam ? (
          <span className="text-text-muted">{t('agents.create.keySuggested')}</span>
        ) : null}
      </div>
      <Help id={`${FIELD_IDS.newTeam}-help`}>{t('agents.create.teamKeyHelp')}</Help>
      <FieldError id={`${FIELD_IDS.newTeam}-error`} message={errorFor('newTeam')} />
      {relationship()}
    </div>
  );

  const executorUnavailable = executorOptions.unavailable.length > 0 && (
    <p className="text-text-muted mt-1.5 text-xs">
      {t('agents.add.notLaunchable', { names: executorOptions.unavailable.map((e) => e.name).join(', ') })}{' '}
      {slug && (
        <Link to={`/orgs/${slug}/settings/executors`} className="text-accent-text underline">
          {t('agents.executor.settingsLink')}
        </Link>
      )}
    </p>
  );
  const executorRadio = (optName: string, label: string) => (
    <OptionCard
      key={optName}
      id={`${FIELD_IDS.executor}-${optName}`}
      name="create-executor"
      value={optName}
      checked={executor === optName}
      title={<span className="font-mono">{label}</span>}
      onChange={() => {
        setExecutor(optName);
        setExecutorPicked(true);
        edited('executor');
      }}
    />
  );
  const executorLabel = (opt: { name: string; kind: string }) =>
    opt.kind === 'custom' ? t('agents.executor.customOption', { name: opt.name }) : opt.name;

  const executorField = (
    <fieldset className="m-0 mt-4 min-w-0 border-0 p-0" aria-describedby={describedBy('executor')}>
      <FieldLabel as="legend" required={required}>{t('agents.executor.label')}</FieldLabel>
      {executorOptions.state === 'loading' ? (
        <div role="status" className="border-border-subtle bg-surface-raised rounded-md border p-3.5" data-create-state="executors-loading">
          <div className="bg-surface-hover mb-2.5 h-3 w-2/5 animate-pulse rounded-sm motion-reduce:animate-none" />
          <span className="text-text-muted text-xs">{t('agents.create.executorsLoading')}</span>
        </div>
      ) : executorOptions.state === 'error' ? (
        <div data-create-state="executors-error">
          <Note
            kind="warn"
            role="alert"
            title={t('agents.create.executorsError', { computer: liveComputer })}
            actions={<Button type="button" size="sm" variant="outline" data-focus-executor onClick={executorOptions.retry}>{t('common.retry')}</Button>}
          />
        </div>
      ) : executorOptions.state === 'empty' ? (
        <div data-create-state="executors-none">
          <Note
            kind="warn"
            title={t('agents.create.executorNone.title', { computer: liveComputer })}
            actions={(
              <>
                {slug && (
                  <Button asChild size="sm" variant="outline">
                    <Link to={`/orgs/${slug}/settings/executors`}>{t('agents.create.openExecutorSettings')}</Link>
                  </Button>
                )}
                <Button type="button" size="sm" variant="outline" data-focus-executor onClick={executorOptions.retry}>{t('common.retry')}</Button>
              </>
            )}
          >
            {t('agents.create.executorNone.body', { computer: liveComputer })}
            {executorUnavailable}
          </Note>
        </div>
      ) : executorOptions.selectable.length === 1 && selectableNames.has(executor) ? (
        <>
          <div className="border-border-subtle bg-surface-raised flex items-center gap-3 rounded-md border px-3.5 py-2.5">
            <span className="text-text-primary font-mono text-sm font-semibold">{executorLabel(executorOptions.selectable[0]!)}</span>
            <span className="text-accent-text ms-auto text-xs font-semibold">✓ {t('agents.create.executorSelected')}</span>
          </div>
          <details className="mt-2" open={execListOpen} onToggle={(event) => setExecListOpen(event.currentTarget.open)}>
            <summary className="text-accent-text cursor-pointer text-xs font-semibold">{t('agents.create.showExecutors')}</summary>
            <div className="mt-2 grid gap-2">
              {executorRadio(executorOptions.selectable[0]!.name, executorLabel(executorOptions.selectable[0]!))}
              <Help>{t('agents.create.executorOnly', { computer: liveComputer })}</Help>
              {executorUnavailable}
            </div>
          </details>
        </>
      ) : (
        <div className="grid gap-2.5 sm:grid-cols-2">
          {executorOptions.selectable.map((opt) => executorRadio(opt.name, executorLabel(opt)))}
          {executorStale && (
            <OptionCard
              id={`${FIELD_IDS.executor}-stale`}
              name="create-executor"
              value={executor}
              checked
              disabled
              unavailable
              title={<span className="font-mono">{t('agents.create.executorUnavailable', { executor })}</span>}
              onChange={() => undefined}
            />
          )}
          <div className="sm:col-span-2">{executorUnavailable}</div>
        </div>
      )}
      <Help id={`${FIELD_IDS.executor}-help`}>{t('agents.create.executorHelp', { computer: liveComputer })}</Help>
      <FieldError id={`${FIELD_IDS.executor}-error`} message={errorFor('executor')} />
    </fieldset>
  );

  // ---- review rail ----
  const reviewTeam = role === 'manager' ? newTeam : team;
  const editButton = (field: CreateField | 'role', label: string) =>
    frozen ? undefined : (
      <button
        type="button"
        onClick={() => focusField(field)}
        aria-label={t('agents.create.review.editField', { field: label })}
        className="text-accent-text min-h-8 px-1 text-xs font-semibold hover:underline"
      >
        {t('agents.create.review.edit')}
      </button>
    );
  const missing = t('agents.create.review.missing');
  const reviewRail = (
    <aside aria-labelledby="create-review-heading" className="border-border-subtle bg-surface-raised border-t px-5 py-5 md:w-80 md:shrink-0 md:border-t-0 md:border-l">
      <div className="md:sticky md:top-5">
        <h3 id="create-review-heading" className="text-text-secondary flex items-center gap-2.5 text-xs font-bold tracking-wider uppercase">
          <span aria-hidden="true" className="bg-text-primary text-surface-canvas inline-grid h-5.5 w-5.5 place-items-center rounded-full text-xs tracking-normal">4</span>
          {t('agents.create.group.review')}
        </h3>
        <p data-testid="create-outcome" className="font-display text-text-primary mt-3 text-lg leading-snug [&_b]:font-semibold">{liveOutcome}</p>
        <dl className="mt-3">
          <ReviewRow
            label={t('agents.create.review.role')}
            value={t(role === 'manager' ? 'agents.role.manager' : 'agents.role.worker')}
            edit={mode === 'agent' ? editButton('role', t('agents.create.review.role')) : undefined}
          />
          <ReviewRow
            label={t('agents.create.review.team')}
            missing={!reviewTeam}
            error={errorFor(role === 'manager' ? 'newTeam' : 'team') !== null}
            value={reviewTeam ? (
              <>
                <span className="font-mono">{reviewTeam}</span>
                {role === 'worker' && teamEntry && (founderTeam(teamEntry) ? ` · ${t('agents.create.founderManaged')}` : teamEntry.manager ? ` · ${t('agents.create.managerOf', { manager: managerLabel(teamEntry) })}` : '')}
              </>
            ) : missing}
            edit={editButton(role === 'manager' ? 'newTeam' : 'team', t('agents.create.review.team'))}
          />
          <ReviewRow label={t('agents.create.review.name')} missing={!name} error={errorFor('name') !== null} value={name || missing} edit={editButton('name', t('agents.create.review.name'))} />
          <ReviewRow label={t('agents.create.review.where')} value={`${shownComputer} · ${shownOrg}`} />
          <ReviewRow
            label={t('agents.create.review.executor')}
            missing={!executor}
            error={errorFor('executor') !== null}
            value={executor ? <span className="font-mono">{executor}</span> : missing}
            edit={editButton('executor', t('agents.create.review.executor'))}
          />
          <ReviewRow
            label={t('agents.create.review.description')}
            missing={!description.trim()}
            error={errorFor('description') !== null}
            value={description.trim() ? description : missing}
            edit={editButton('description', t('agents.create.review.description'))}
          />
          <ReviewRow
            label={t('agents.create.review.instructions')}
            missing={!systemPrompt.trim()}
            error={errorFor('systemPrompt') !== null}
            value={systemPrompt.trim() ? <span className={fullInstructions ? 'whitespace-pre-wrap' : 'line-clamp-3'}>{systemPrompt}</span> : missing}
            edit={editButton('systemPrompt', t('agents.create.review.instructions'))}
          />
        </dl>
        {systemPrompt.trim() !== '' && (
          <button
            type="button"
            aria-expanded={fullInstructions}
            onClick={() => setFullInstructions((v) => !v)}
            className="text-accent-text mt-1.5 text-sm font-semibold underline underline-offset-2"
          >
            {t(fullInstructions ? 'agents.create.review.hideInstructions' : 'agents.create.review.showInstructions')}
          </button>
        )}
        <p className="text-text-muted mt-4 text-xs leading-relaxed">
          {t('agents.create.review.note', { action: actionLabel })} {t('agents.create.review.active')}
        </p>
      </div>
    </aside>
  );

  const editForm = (
    <form id="create-agent-form" onSubmit={onSubmit} onKeyDown={onFormKeyDown} noValidate>
      {banners.length > 0 && <div className="grid gap-2.5 px-5 pt-4 sm:px-7" data-testid="create-banners">{banners}</div>}
      <div className="md:flex md:min-h-full">
        <fieldset disabled={frozen} className="m-0 min-w-0 border-0 px-5 pt-1 pb-8 sm:px-7 md:flex-1">
          {/* 1 · Team and role */}
          <fieldset className="border-border-subtle m-0 min-w-0 border-0 border-b px-0 pt-5 pb-5">
            <GroupLegend n={1}>{t('agents.create.group.relationship')}</GroupLegend>
            {mode === 'agent' ? (
              <fieldset className="m-0 mt-4 min-w-0 border-0 p-0">
                <legend className="text-text-primary mb-1.5 p-0 text-sm font-semibold">{t('agents.create.question')}</legend>
                <div className="grid gap-2.5 sm:grid-cols-2">
                  <OptionCard
                    id="create-role-worker"
                    name="create-role"
                    value="worker"
                    checked={role === 'worker'}
                    title={t('agents.create.outcome.worker')}
                    detail={t('agents.create.outcome.workerHint')}
                    onChange={() => onRoleChange('worker')}
                  />
                  <OptionCard
                    id="create-role-manager"
                    name="create-role"
                    value="manager"
                    checked={role === 'manager'}
                    title={t('agents.create.outcome.manager')}
                    detail={t('agents.create.outcome.managerHint')}
                    onChange={() => onRoleChange('manager')}
                  />
                </div>
              </fieldset>
            ) : (
              <FixedRelationship title={t('agents.create.fixed.title')} body={t('agents.create.fixed.body')} />
            )}
            {role === 'worker' ? (
              <>
                {teamPicker}
                {nameField}
              </>
            ) : (
              <>
                {nameField}
                {teamKeyField}
              </>
            )}
          </fieldset>

          {/* 2 · Agent responsibilities */}
          <fieldset className="border-border-subtle m-0 min-w-0 border-0 border-b px-0 pt-5 pb-5">
            <GroupLegend n={2}>{t('agents.create.group.responsibilities')}</GroupLegend>
            <div className="mt-4">
              <FieldLabel htmlFor={FIELD_IDS.description} required={required}>{t('agents.create.descriptionLabel')}</FieldLabel>
              <Input
                id={FIELD_IDS.description}
                value={description}
                onChange={(e) => {
                  setDescription(e.target.value);
                  edited('description');
                }}
                autoComplete="off"
                className={INPUT_CLASS}
                aria-required="true"
                aria-invalid={errorFor('description') !== null}
                aria-describedby={describedBy('description')}
              />
              <Help id={`${FIELD_IDS.description}-help`}>{t('agents.create.descriptionHelp')}</Help>
              <FieldError id={`${FIELD_IDS.description}-error`} message={errorFor('description')} />
            </div>
            <div className="mt-4">
              <FieldLabel htmlFor={FIELD_IDS.systemPrompt} required={required}>{t('agents.create.instructionsLabel')}</FieldLabel>
              <Textarea
                id={FIELD_IDS.systemPrompt}
                value={systemPrompt}
                onChange={(e) => {
                  setSystemPrompt(e.target.value);
                  edited('systemPrompt');
                }}
                rows={6}
                className="bg-surface-canvas min-h-36 leading-relaxed"
                aria-required="true"
                aria-invalid={errorFor('systemPrompt') !== null}
                aria-describedby={describedBy('systemPrompt')}
              />
              <Help id={`${FIELD_IDS.systemPrompt}-help`}>{t('agents.create.instructionsHelp')}</Help>
              <FieldError id={`${FIELD_IDS.systemPrompt}-error`} message={errorFor('systemPrompt')} />
              <details className="mt-2" open={exampleOpen} onToggle={(event) => setExampleOpen(event.currentTarget.open)}>
                <summary className="text-accent-text cursor-pointer text-xs font-semibold">{t('agents.create.seeExample')}</summary>
                <div className="border-border-subtle bg-surface-raised mt-2 rounded-md border p-3 text-sm">
                  <span className="text-text-muted block text-xs">{t('agents.create.exampleLabel')}</span>
                  <span className="text-text-secondary">{t(role === 'manager' ? 'agents.create.example.manager' : 'agents.create.example.worker')}</span>
                </div>
              </details>
            </div>
          </fieldset>

          {/* 3 · Runtime */}
          <fieldset className="m-0 min-w-0 border-0 px-0 pt-5 pb-2">
            <GroupLegend n={3}>{t('agents.create.group.runtime')}</GroupLegend>
            {executorField}
          </fieldset>
        </fieldset>
        {reviewRail}
      </div>
    </form>
  );

  const resultView = resultKind !== null;
  const unresolvedResult = unknown || resultKind === 'setupNoId';

  return (
    <Dialog open={open && !orgChanged} onOpenChange={requestOpenChange}>
      {/* Phone: full-height sheet. Desktop: right-anchored drawer, 12px inset, at most 960px (max-w-240). */}
      {/* eslint-disable-next-line tailwindcss/no-arbitrary-value -- design v1.1 drawer width clamp to the viewport minus its 12px insets (precedent: AgentDetailDrawer) */}
      <DialogContent className="inset-0 flex h-dvh max-h-none w-full max-w-none translate-x-0 translate-y-0 flex-col gap-0 overflow-hidden rounded-none p-0 md:inset-y-3 md:right-3 md:left-auto md:h-auto md:w-[calc(100vw-1.5rem)] md:max-w-240 md:rounded-lg [&>button:last-child]:top-5 [&>button:last-child]:right-5"
        closeLabel={t('agents.create.close')}
        data-create-surface
        onOpenAutoFocus={(event) => {
          event.preventDefault();
          initialFocus.current = true;
        }}
        onEscapeKeyDown={(event) => {
          if (menuOpen) {
            event.preventDefault();
            closeMenu();
          }
        }}
      >
        <header className="border-border-subtle shrink-0 border-b px-5 pt-4 pb-3.5 sm:px-6">
          <DialogTitle className="font-display pe-10 text-2xl font-normal">
            {t(mode === 'team' ? 'agents.create.titleTeam' : 'agents.create.titleAgent')}
          </DialogTitle>
          <div className="mt-2.5 flex flex-wrap gap-2 text-xs" data-testid="create-context">
            <span className={`inline-flex items-center gap-1.5 rounded-full border px-2.5 py-0.5 ${targetBlock === 'remote' ? 'bg-info-soft border-info-soft' : targetBlock !== null ? 'bg-attention-soft text-attention-text border-attention-soft' : 'border-border-subtle bg-surface-raised text-text-secondary'}`}>
              {t('agents.create.ctx.computer')} <b className="text-text-primary font-semibold">{shownComputer}</b>
              {targetBlock === 'remote' && <span>· {t('agents.create.ctx.remote')}</span>}
            </span>
            <span className="border-border-subtle bg-surface-raised text-text-secondary inline-flex items-center gap-1.5 rounded-full border px-2.5 py-0.5">
              {t('agents.create.ctx.org')} <b className="text-text-primary font-semibold">{shownOrg}</b>
            </span>
          </div>
          <DialogDescription className="mt-2">
            {t(outcomeRole === 'manager' ? 'agents.create.lede.team' : 'agents.create.lede.worker')}
          </DialogDescription>
        </header>

        <div ref={bodyRef} className="min-h-0 flex-1 overflow-y-auto overscroll-contain" data-create-body>
          {resultView ? <div className="mx-auto max-w-155 px-5 py-8 sm:px-7">{resultPanel()}</div> : editForm}
        </div>

        <footer className="border-border-subtle bg-surface-sunken shrink-0 border-t px-5 py-3 sm:px-6">
          {resultView ? (
            <div className="flex flex-wrap items-center gap-3">
              <p className="text-text-secondary min-w-0 flex-1 text-sm">{unresolvedResult ? t('agents.create.result.closeNote') : ''}</p>
              <Button type="button" variant="outline" onClick={() => requestOpenChange(false)}>
                {t(unresolvedResult ? 'agents.create.close' : 'agents.create.done')}
              </Button>
            </div>
          ) : (
            <>
              <p className="text-text-primary mb-2 text-sm font-semibold break-words md:hidden [&_b]:font-bold" aria-hidden="true">{liveOutcome}</p>
              <div className="flex flex-wrap items-center gap-3">
                <p
                  id="create-footer-status"
                  role="status"
                  data-testid="create-footer-status"
                  className={`min-w-0 flex-1 basis-48 text-sm break-words ${footerMessage.tone === 'err' ? 'text-danger font-semibold' : footerMessage.tone === 'warn' ? 'text-attention-text' : 'text-text-secondary'}`}
                >
                  {footerMessage.text}
                </p>
                <div className="flex flex-1 basis-full gap-3 sm:flex-none sm:basis-auto">
                  <Button type="button" variant="outline" className="flex-1 sm:flex-none" onClick={() => requestOpenChange(false)}>
                    {t(busy ? 'agents.create.close' : 'common.cancel')}
                  </Button>
                  <Button
                    type="submit"
                    form="create-agent-form"
                    className="flex-1 aria-disabled:opacity-60 sm:flex-none"
                    aria-disabled={blocked || undefined}
                    aria-describedby="create-footer-status"
                    data-testid="create-submit"
                  >
                    {busy && <Loader2 aria-hidden="true" className="animate-spin motion-reduce:animate-none" />}
                    {busy ? t('agents.add.creating') : actionLabel}
                  </Button>
                </div>
              </div>
            </>
          )}
        </footer>
      </DialogContent>
    </Dialog>
  );
}
