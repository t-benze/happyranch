/**
 * Pure presentation model for the desktop Connection dialog (THR-387 S6-W3;
 * handoff v2.4 §2-§5, corrections C3 and C6).
 *
 * Inputs are native observations only: the #1774 `app.status` store (target,
 * state, candidate, incompatible, local, access_unsaved) and the v1
 * `client.*`/`host.*` snapshots and results. Outputs are catalog keys and
 * flags. Nothing here infers Connected, edits a list optimistically or
 * claims "nothing changed" unless native reported `effect: "none"`.
 *
 * No React/DOM and only type imports: checked by
 * `web/scripts/connection-dialog.test.mjs`.
 */
import type { MessageKey } from '@/lib/i18n';
import type { AppCandidate, AppIncompatible, AppState } from '@/lib/native/appStatus';
import type {
  ActionResult,
  ClientCommand,
  ClientStage,
  ConnectionCommand,
  ConnectionFailure,
  HostCommand,
  HostSnapshot,
  SavedAccess,
} from '@/lib/native/connection';

export type Tone = 'neutral' | 'progress' | 'attention' | 'danger' | 'ok';

// ---- Active target summary ----

export interface CompatibilityView {
  title: MessageKey;
  body: MessageKey;
  /** Offer "Choose another computer" (focus the Connect section). */
  chooseAnother: boolean;
}

export interface TargetView {
  badge: MessageKey;
  tone: Tone;
  title: MessageKey;
  body: MessageKey;
  /** Re-read `app.status` (Recheck / Reconnect the same target). */
  recheck: boolean;
  /** The active remote session can be disconnected (owner gate first). */
  disconnect: boolean;
  /** Connection health facts (`client.health`) apply. */
  health: boolean;
  compatibility: CompatibilityView | null;
}

/**
 * C6 which-side recovery (handoff §5; prep §6). The needs-update screen alone
 * offers app.relaunch when app_relaunch is true; this dialog keeps Recheck.
 */
export function compatibilityView(incompatible: AppIncompatible, targetKind: 'local' | 'remote' | null): CompatibilityView {
  if (incompatible.side === 'app') {
    return { title: 'connection.compat.app.title', body: 'connection.compat.app.body', chooseAnother: true };
  }
  if (incompatible.side === 'host') {
    if (targetKind === 'local') {
      return { title: 'connection.compat.localService.title', body: 'connection.compat.localService.body', chooseAnother: true };
    }
    return {
      title: 'connection.compat.host.title',
      body: incompatible.host_contract === null ? 'connection.compat.host.noMetadata' : 'connection.compat.host.older',
      chooseAnother: true,
    };
  }
  return { title: 'connection.compat.unknown.title', body: 'connection.compat.unknown.body', chooseAnother: true };
}

export function targetView(app: AppState): TargetView {
  const remote = app.target?.kind === 'remote';
  const base = { recheck: false, disconnect: remote, health: false, compatibility: null };
  switch (app.state) {
    case 'no_target':
      return { ...base, disconnect: false, badge: 'connection.state.notConnected', tone: 'neutral', title: 'connection.target.none.title', body: 'connection.target.none.body' };
    case 'target_starting':
      return { ...base, recheck: true, badge: 'connection.state.starting', tone: 'progress', title: 'connection.target.starting.title', body: 'connection.target.starting.body' };
    case 'ready':
      return remote
        ? { ...base, health: true, badge: 'connection.state.connected', tone: 'ok', title: 'connection.target.remoteReady.title', body: 'connection.target.remoteReady.body' }
        : { ...base, badge: 'connection.state.running', tone: 'ok', title: 'connection.target.localReady.title', body: 'connection.target.localReady.body' };
    case 'busy':
      return { ...base, badge: 'connection.state.busy', tone: 'attention', title: 'connection.target.busy.title', body: 'connection.target.busy.body' };
    case 'unreachable':
      if (!remote && app.local.running === 'stopped') {
        // Spec §2: name it, keep the selection, start nothing on open.
        return { ...base, recheck: true, badge: 'connection.state.notRunning', tone: 'attention', title: 'connection.target.localStopped.title', body: 'connection.target.localStopped.body' };
      }
      return { ...base, recheck: true, badge: 'connection.state.unreachable', tone: 'danger', title: 'connection.target.unreachable.title', body: 'connection.target.unreachable.body' };
    case 'incompatible': {
      const compatibility = app.incompatible ? compatibilityView(app.incompatible, app.target?.kind ?? null) : null;
      return {
        ...base,
        recheck: true,
        compatibility,
        badge: app.incompatible?.side === 'unknown' ? 'connection.state.compatibilityUnknown' : 'connection.state.needsUpdate',
        tone: 'attention',
        title: compatibility?.title ?? 'connection.compat.unknown.title',
        body: compatibility?.body ?? 'connection.compat.unknown.body',
      };
    }
  }
}

// ---- Candidate (connection attempt) ----

export interface CandidateView {
  badge: MessageKey;
  tone: Tone;
  title: MessageKey;
  lead: MessageKey;
  /** Explicit Cancel attempt is offered (in flight or ready). */
  cancel: boolean;
  /** Ready to switch: the explicit Switch action. Never "Connected". */
  switchable: boolean;
  /** Failed: Try again (`client.retry`). */
  retry: boolean;
  /** The attempt is over: a new invite may be pasted. */
  finished: boolean;
}

const IN_FLIGHT: readonly AppCandidate['phase'][] = ['signing_in', 'awaiting_approval'];

/** True while the attempt can still be cancelled (in flight or ready to switch). */
export function candidateCancellable(candidate: AppCandidate | null): boolean {
  return !!candidate && (IN_FLIGHT.includes(candidate.phase) || candidate.phase === 'ready_to_switch');
}

export function candidateView(candidate: AppCandidate, hasActive: boolean): CandidateView {
  const still: MessageKey = hasActive ? 'connection.candidate.stillUsing' : 'connection.candidate.notConnected';
  switch (candidate.phase) {
    case 'signing_in':
      return {
        badge: 'connection.candidate.badge.signingIn',
        tone: 'progress',
        title: 'connection.candidate.signingIn.title',
        lead: hasActive ? 'connection.candidate.connectingStillUsing' : 'connection.candidate.connecting',
        cancel: true,
        switchable: false,
        retry: false,
        finished: false,
      };
    case 'awaiting_approval':
      return {
        badge: 'connection.candidate.badge.awaitingApproval',
        tone: 'attention',
        title: 'connection.candidate.awaitingApproval.title',
        lead: hasActive ? 'connection.candidate.connectingStillUsing' : 'connection.candidate.connecting',
        cancel: true,
        switchable: false,
        retry: false,
        finished: false,
      };
    case 'ready_to_switch':
      return {
        badge: 'connection.candidate.badge.readyToSwitch',
        tone: 'ok',
        title: 'connection.candidate.ready.title',
        lead: hasActive ? 'connection.candidate.readyStillUsing' : 'connection.candidate.readyNoActive',
        cancel: true,
        switchable: true,
        retry: false,
        finished: false,
      };
    case 'failed':
      return { badge: 'connection.candidate.badge.failed', tone: 'danger', title: 'connection.candidate.failed.title', lead: still, cancel: false, switchable: false, retry: true, finished: true };
    case 'cancelled':
      return { badge: 'connection.candidate.badge.cancelled', tone: 'neutral', title: 'connection.candidate.cancelled.title', lead: still, cancel: false, switchable: false, retry: false, finished: true };
  }
}

// ---- Attempt steps (from the v1 client stage) ----

export type StepState = 'done' | 'current' | 'todo' | 'unsaved';

export interface StepRow {
  label: MessageKey;
  state: StepState;
  /** Suffix after the label (done / in progress / not saved ...), null for todo. */
  note: MessageKey | null;
}

const STEPS: readonly MessageKey[] = [
  'connection.client.stepJoin',
  'connection.client.stepRequest',
  'connection.client.stepWait',
  'connection.client.stepSecure',
  'connection.client.stepOpen',
];
const SECURE_STEP = 3;
const STEP_OF: Partial<Record<ClientStage, number>> = { joining: 0, pairing: 1, waitingApproval: 2, saving: 3, opening: 4 };
const ATTEMPT_STAGES: readonly ClientStage[] = ['validating', 'joining', 'pairing', 'waitingApproval', 'saving', 'opening'];

export function isAttemptStage(stage: ClientStage): boolean {
  return ATTEMPT_STAGES.includes(stage);
}

/** Steps for an in-flight stage; `[]` while validating or outside an attempt. */
export function stepRows(stage: ClientStage, savedAccess: SavedAccess): StepRow[] {
  const current = STEP_OF[stage];
  if (current === undefined) return [];
  return STEPS.map((label, index) => {
    if (index > current) return { label, state: 'todo', note: null };
    if (index === current) return { label, state: 'current', note: 'connection.client.stepCurrent' };
    // Storage is done only when native positively reports saved access.
    if (index === SECURE_STEP && savedAccess !== 'saved') {
      const note: MessageKey =
        savedAccess === 'none'
          ? 'connection.client.stepSkipped'
          : savedAccess === 'notSaved' || savedAccess === 'needsPermission'
            ? 'connection.client.stepNotSaved'
            : 'connection.client.stepUnconfirmed';
      return { label, state: 'unsaved', note };
    }
    return { label, state: 'done', note: 'connection.client.stepDone' };
  });
}

/** The Cancel hint: storage finishes at a safe point; otherwise local-only cancel. */
export function cancelHint(stage: ClientStage | null): MessageKey {
  return stage === 'saving' ? 'connection.client.cancelSafePoint' : 'connection.client.cancelLocalOnly';
}

// ---- Target commit refusals (client.commitSwitch / client.disconnect) ----

export type CommitAction = 'switch' | 'disconnect';

export interface RefusalView {
  /** "Not switched." / "Couldn't confirm whether it switched." ... */
  lead: MessageKey;
  reason: MessageKey;
  tone: Tone;
  /** List the affected Main/Settings owners (page · computer · org). */
  showOwners: boolean;
}

const REFUSAL: Record<string, MessageKey> = {
  owner_save_pending: 'connection.refusal.ownerSavePending',
  owner_save_unknown: 'connection.refusal.ownerSaveUnknown',
  owner_edits_pending: 'connection.refusal.ownerEditsPending',
  non_get_pending: 'connection.refusal.nonGetPending',
  target_starting: 'connection.refusal.targetStarting',
  target_unreachable: 'connection.refusal.targetUnreachable',
  documentChanged: 'connection.refusal.changed',
  stateChanged: 'connection.refusal.changed',
  busy: 'connection.refusal.busy',
  receipt: 'connection.refusal.receipt',
};
const OWNER_CODES: readonly string[] = ['owner_save_pending', 'owner_save_unknown', 'owner_edits_pending'];

/**
 * Named copy for every commit refusal. There is no automatic retry: the user
 * acts again after reading it. An unknown effect is never "not switched".
 * `receipt` is the web-side hold: native did not acknowledge the owner summary,
 * so nothing was sent.
 */
export function refusalView(failure: ConnectionFailure, action: CommitAction): RefusalView {
  const unknown = failure.effect === 'unknown';
  const lead: MessageKey = unknown
    ? action === 'switch'
      ? 'connection.refusal.switchUnknown'
      : 'connection.refusal.disconnectUnknown'
    : action === 'switch'
      ? 'connection.refusal.notSwitched'
      : 'connection.refusal.notDisconnected';
  let reason: MessageKey;
  if (failure.code === 'incompatible') {
    reason =
      failure.side === 'host'
        ? 'connection.refusal.incompatibleHost'
        : failure.side === 'app'
          ? 'connection.refusal.incompatibleApp'
          : 'connection.refusal.incompatibleUnknown';
  } else if (failure.code === 'intent_changed') {
    // Web-side: the chosen candidate/target changed before anything was sent (F1).
    reason = action === 'switch' ? 'connection.refusal.switchChoiceChanged' : 'connection.refusal.disconnectChoiceChanged';
  } else if (unknown) {
    reason = 'connection.refusal.checkStatus';
  } else {
    reason = REFUSAL[failure.code] ?? 'connection.refusal.other';
  }
  return { lead, reason, tone: unknown ? 'attention' : 'danger', showOwners: OWNER_CODES.includes(failure.code) };
}

// ---- Ordinary action outcomes (client.* / host.*) ----

export interface OutcomeLine {
  key: MessageKey;
  /** Interpolate the action label (`{action}`). */
  action?: boolean;
  /** Interpolate the record name captured at the action (`{name}`). */
  name?: boolean;
}

export interface OutcomeView {
  lines: OutcomeLine[];
  tone: Tone;
  /** Check again (re-read native state). */
  checkAgain: boolean;
  /** Explicit retry of the same command and record. */
  retry: boolean;
  hints: MessageKey[];
  /** Turn off / Revoke component results. */
  components: boolean;
}

export const ACTION_LABEL: Record<ConnectionCommand, MessageKey> = {
  'client.status': 'connection.client.checkAgain',
  'client.savedComputers': 'connection.client.checkAgain',
  'client.acceptInvite': 'connection.client.acceptInvite',
  'client.connectSaved': 'connection.client.connectSaved',
  'client.removeSaved': 'connection.client.removeSaved',
  'client.cancelAttempt': 'connection.candidate.cancel',
  'client.retry': 'connection.client.retryAttempt',
  'client.retrySave': 'connection.access.retry',
  'client.health': 'connection.client.checkHealth',
  'client.commitSwitch': 'connection.candidate.switch',
  'client.disconnect': 'connection.client.disconnect',
  'host.status': 'connection.client.checkAgain',
  'host.enable': 'connection.host.hTurnOn',
  'host.turnOff': 'connection.host.hTurnOff',
  'host.createInvite': 'connection.host.hAddDevice',
  'host.approveDevice': 'connection.host.hApprove',
  'host.rejectDevice': 'connection.host.hReject',
  'host.nextDevice': 'connection.host.hNextDevice',
  'host.revokeDevice': 'connection.host.hRevoke',
};

const CLIENT_ERROR: Record<string, MessageKey> = {
  busy: 'connection.err.busy',
  permissionDenied: 'connection.err.permissionDenied',
  stateChanged: 'connection.err.stateChanged',
  documentChanged: 'connection.err.stateChanged',
  expired: 'connection.err.expired',
  unavailable: 'connection.err.unavailable',
  outcomeUnknown: 'connection.err.outcomeUnknown',
  invalidInvite: 'connection.err.invalidInvite',
  newInviteRequired: 'connection.err.newInvite',
  networkUnavailable: 'connection.err.network',
  trustMismatch: 'connection.err.trust',
  identityChanged: 'connection.err.identity',
  runtimeUnavailable: 'connection.err.runtime',
  accessDenied: 'connection.err.accessDenied',
  revokedConfirmed: 'connection.err.revoked',
  keychainLocked: 'connection.err.locked',
  keychainDenied: 'connection.err.storageDenied',
  saveFailed: 'connection.err.saveFailed',
  removalIncomplete: 'connection.err.removalIncomplete',
};
const HOST_ERROR: Record<string, MessageKey> = {
  ...CLIENT_ERROR,
  stateChanged: 'connection.host.hErrStateChanged',
  documentChanged: 'connection.host.hErrStateChanged',
  expired: 'connection.host.hErrExpired',
  runtimeUnavailable: 'connection.host.hErrRuntime',
  hostStateUnrecognized: 'connection.host.hErrStateUnrecognized',
};

const HOST_STATE_ERROR: Record<string, MessageKey> = {
  residue: 'connection.host.hErrStateResidue', owner: 'connection.host.hErrStateOwner',
  provenance: 'connection.host.hErrStateProvenance', custody: 'connection.host.hErrStateCustody',
  inventory: 'connection.host.hErrStateInventory', quiescence: 'connection.host.hErrStateQuiescence',
  timeout: 'connection.host.hErrStateTimeout', unknown: 'connection.host.hErrStateUnknown',
};

export function errorKey(command: ConnectionCommand, code: string, category?: ConnectionFailure['category']): MessageKey {
  if (command === 'host.enable' && code === 'enablePreparationFailed' && category) {
    return ('connection.host.enableFailure.' + category) as MessageKey;
  }
  if (command.startsWith('host.') && code === 'hostStateUnrecognized' && category) {
    return HOST_STATE_ERROR[category] ?? HOST_STATE_ERROR.unknown;
  }
  const table = command.startsWith('host.') ? HOST_ERROR : CLIENT_ERROR;
  return table[code] ?? 'connection.err.unavailable';
}

/** Commands whose "No" in the native confirmation returns `effect: "none"`. */
const NOTHING_CHANGED: readonly ConnectionCommand[] = [
  'client.cancelAttempt',
  'client.disconnect',
  'client.removeSaved',
  'host.enable',
  'host.turnOff',
  'host.createInvite',
  'host.approveDevice',
  'host.rejectDevice',
  'host.revokeDevice',
];

function confirmedLine(command: ConnectionCommand, result: ActionResult, savedAccess: SavedAccess | null): { line: OutcomeLine; tone: Tone } | null {
  switch (command) {
    case 'client.cancelAttempt':
      return { line: { key: 'connection.client.cancelledConfirmed' }, tone: 'ok' };
    case 'client.disconnect':
      return { line: { key: savedAccess === 'saved' ? 'connection.client.disconnectedSaved' : 'connection.client.disconnectedUnsaved' }, tone: 'ok' };
    case 'client.removeSaved':
      return result.localCleanup === 'incomplete' || result.localCleanup === 'unknown'
        ? { line: { key: 'connection.err.removalIncomplete' }, tone: 'attention' }
        : { line: { key: 'connection.client.removedConfirmed', name: true }, tone: 'ok' };
    case 'client.retrySave':
      return { line: { key: 'connection.client.savedConfirmed' }, tone: 'ok' };
    case 'host.enable':
      return { line: { key: 'connection.host.hEnableConfirmed' }, tone: 'ok' };
    case 'host.turnOff': {
      const complete = [result.hostDenial, result.hostCleanup, result.networkCleanup].every((v) => v === 'confirmed' || v === 'notApplicable');
      return { line: { key: 'connection.host.hTurnOffConfirmed' }, tone: complete ? 'ok' : 'attention' };
    }
    case 'host.createInvite':
      return result.inviteState === 'issued'
        ? { line: { key: 'connection.host.hInviteCreated' }, tone: 'ok' }
        : { line: { key: 'connection.host.hInviteUnconfirmed' }, tone: 'attention' };
    case 'host.approveDevice':
      return { line: { key: 'connection.host.hApproved', name: true }, tone: 'ok' };
    case 'host.rejectDevice':
      return { line: { key: 'connection.host.hRejected', name: true }, tone: 'ok' };
    case 'host.revokeDevice':
      return result.hostDenial === 'confirmed'
        ? { line: { key: 'connection.host.hRevoked', name: true }, tone: 'ok' }
        : { line: { key: 'connection.host.hRevokeUnconfirmed', name: true }, tone: 'attention' };
    default:
      return null;
  }
}

/**
 * Outcome copy for a v1 action, or null when there is nothing to say (an
 * attempt that started: its progress shows in the candidate panel).
 * `savedAccess` is the latest client observation (Disconnect copy).
 */
export function outcomeView(
  command: ClientCommand | HostCommand,
  outcome: { result: ActionResult } | { failure: ConnectionFailure },
  savedAccess: SavedAccess | null,
): OutcomeView | null {
  const components = command === 'host.turnOff' || command === 'host.revokeDevice';
  if ('result' in outcome) {
    const { result } = outcome;
    if (result.effect === 'unknown') {
      // Unknown is never "nothing changed" and never a success claim (M9).
      return { lines: [{ key: 'connection.client.resultUnknown', action: true }], tone: 'attention', checkAgain: true, retry: false, hints: [], components };
    }
    if (result.effect === 'confirmed') {
      const confirmed = confirmedLine(command, result, savedAccess);
      if (!confirmed) return null;
      return { lines: [confirmed.line], tone: confirmed.tone, checkAgain: false, retry: false, hints: [], components };
    }
    if (NOTHING_CHANGED.includes(command)) {
      return { lines: [{ key: 'connection.client.nothingChanged', action: true }], tone: 'neutral', checkAgain: false, retry: false, hints: [], components };
    }
    return null;
  }
  const { failure } = outcome;
  if (command === 'host.enable' && failure.code === 'enablePreparationFailed' && failure.category) {
    return { lines: [{ key: 'connection.host.enableFailed' },
      { key: errorKey(command, failure.code, failure.category) }, { key: 'connection.client.effectUnknown' }],
      tone: 'danger', checkAgain: true, retry: false, hints: [], components: false };
  }
  const lines: OutcomeLine[] = [{ key: 'connection.client.failedAction', action: true }, { key: errorKey(command, failure.code, failure.category), name: true }];
  if (failure.effect === 'unknown' && failure.code !== 'outcomeUnknown') lines.push({ key: 'connection.client.effectUnknown' });
  const hints: MessageKey[] = [];
  if (failure.recovery === 'newInviteOnHost') hints.push(command.startsWith('host.') ? 'connection.host.hNewInviteHint' : 'connection.client.newInviteHint');
  if (failure.recovery === 'nativeRecovery' || failure.recovery === 'operatorRecovery') hints.push('connection.client.nativeRecoveryHint');
  return {
    lines,
    tone: failure.effect === 'unknown' ? 'attention' : 'danger',
    checkAgain: failure.recovery === 'refresh' || failure.effect === 'unknown',
    retry: failure.recovery === 'explicitRetry' || failure.recovery === 'nativePermission',
    hints,
    components: false,
  };
}

// ---- Host management (v1 host snapshot) ----

export interface HostView {
  badge: MessageKey;
  tone: Tone;
  title: MessageKey;
  lead: MessageKey;
  /** `{name}` in the title: the requesting device label. */
  requester: string | null;
  enable: boolean;
  approve: boolean;
  createInvite: boolean;
  checkAgain: boolean;
  /** Devices list, selection and Turn off apply (ready / paused). */
  manage: boolean;
  invite: 'issued' | 'expired' | 'used' | null;
  hints: MessageKey[];
  stale: boolean;
}

/**
 * Host presentation (port of `web/connection/src/host.ts`). `unconfirmed`:
 * an uncertain action whose re-read failed, so the retained snapshot no
 * longer proves the current state: show "not confirmed" and no lifecycle
 * controls until a fresh observation.
 */
export function hostView(snapshot: HostSnapshot, unconfirmed: boolean): HostView {
  const phase = unconfirmed || snapshot.observation === 'unavailable' ? 'unknown' : snapshot.setup;
  const requester = phase === 'ready' ? (snapshot.pending?.label ?? null) : null;
  const runtime = snapshot.observation === 'known' ? snapshot.runtime : 'unknown';
  const issued = snapshot.inviteState === 'issued';
  const table: Record<HostSnapshot['setup'], [MessageKey, Tone, MessageKey, MessageKey]> = {
    off: ['connection.host.hBadgeOff', 'neutral', 'connection.host.hTitleOff', 'connection.host.hLeadOff'],
    pending: ['connection.host.hBadgeSettingUp', 'progress', 'connection.host.hTitlePending', 'connection.host.hLeadPending'],
    ready: ['connection.host.hBadgeReady', 'ok', 'connection.host.hTitleReady', 'connection.host.hLeadReady'],
    paused: ['connection.host.hBadgePaused', 'danger', 'connection.host.hTitlePaused', 'connection.host.hLeadPaused'],
    failed: ['connection.host.hBadgeRepair', 'attention', 'connection.host.hTitleFailed', 'connection.host.hLeadFailed'],
    unknown: ['connection.host.hBadgeUnknown', 'neutral', 'connection.host.hTitleUnknown', 'connection.host.hLeadUnknown'],
  };
  let [badge, tone, title, lead] = table[phase];
  if (unconfirmed) lead = 'connection.host.hLeadUnconfirmed';
  else if (requester !== null) {
    badge = 'connection.host.hBadgeApproval';
    tone = 'attention';
    title = 'connection.host.hTitleRequest';
    lead = 'connection.host.hLeadRequest';
  } else if (phase === 'ready' && (runtime === 'offline' || runtime === 'restarting')) {
    badge = runtime === 'offline' ? 'connection.host.hBadgeOffline' : 'connection.host.hBadgeRestarting';
    tone = 'attention';
    title = runtime === 'offline' ? 'connection.host.hTitleOffline' : 'connection.host.hTitleRestarting';
    lead = 'connection.host.hLeadOffline';
  } else if (phase === 'ready' && issued) {
    badge = 'connection.host.hBadgeInvite';
    tone = 'progress';
    title = 'connection.host.hInviteActive';
    lead = 'connection.host.hInviteNative';
  }
  const hints: MessageKey[] = [];
  if (phase === 'pending') hints.push('connection.host.hCancelNative');
  if (phase === 'failed') hints.push('connection.host.hRepairUnavailable', 'connection.client.nativeRecoveryHint');
  if (phase === 'paused') hints.push('connection.host.hRenewUnavailable');
  return {
    badge,
    tone,
    title,
    lead,
    requester,
    enable: phase === 'off',
    approve: requester !== null,
    createInvite: phase === 'ready' && requester === null && !issued,
    checkAgain: phase === 'pending' || phase === 'failed' || phase === 'unknown' || phase === 'paused',
    manage: phase === 'ready' || phase === 'paused',
    invite: phase !== 'ready' ? null : issued ? 'issued' : snapshot.inviteState === 'expired' ? 'expired' : snapshot.inviteState === 'used' ? 'used' : null,
    hints,
    stale: snapshot.observation === 'stale',
  };
}

// ---- Names ----

/**
 * Current-locale name for a native label. Linux-generated "Saved computer N"
 * labels are re-localized (EM M15, as `web/connection/src/client.ts` does);
 * every other label is shown verbatim.
 */
export function displayLabel(raw: string, savedComputerWord: string, locale: string): string {
  const generated = /^Saved computer ([1-9][0-9]{0,8})$/.exec(raw);
  if (!generated) return raw;
  return `${savedComputerWord} ${new Intl.NumberFormat(locale).format(Number(generated[1]))}`;
}
