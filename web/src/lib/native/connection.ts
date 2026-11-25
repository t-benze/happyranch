/**
 * Typed native Connection commands for the desktop Connection dialog
 * (THR-387 S6-W3; bridge spec §12 "Main document admission").
 *
 * Main admits the v1 `client.*` and `host.*` commands (#1782) plus
 * `client.commitSwitch`. Results keep their v1 shapes
 * (`protocol/native-connection-bridge.v1.json` `$defs`); refusals arrive as
 * the Main error envelope `{code, effect, recovery[, side]}`.
 *
 * - Every call goes through the ONE detected bridge (`nativeBridge()` in
 *   `./target`), so `__setNativeBridgeForTests` covers it. Nothing here reads
 *   `window.happyranchConnection` directly.
 * - Parsers accept only the closed shapes the dialog uses. Anything else is
 *   `null` (results) or the closed fallback failure (refusals); raw native
 *   text is never shown.
 * - No state lives here: the dialog re-reads native state on open and after
 *   every action, so closing it drops nothing native holds.
 *
 * Pure apart from the bridge accessor (no React/DOM).
 */
import { nativeBridge } from './target';

export type ClientCommand =
  | 'client.status'
  | 'client.savedComputers'
  | 'client.acceptInvite'
  | 'client.connectSaved'
  | 'client.removeSaved'
  | 'client.cancelAttempt'
  | 'client.retry'
  | 'client.retrySave'
  | 'client.health'
  | 'client.commitSwitch'
  | 'client.disconnect';

export type HostCommand =
  | 'host.status'
  | 'host.enable'
  | 'host.turnOff'
  | 'host.createInvite'
  | 'host.approveDevice'
  | 'host.rejectDevice'
  | 'host.nextDevice'
  | 'host.revokeDevice';

export type ConnectionCommand = ClientCommand | HostCommand;

export type Effect = 'none' | 'confirmed' | 'unknown';
export type Recovery =
  | 'refresh'
  | 'explicitRetry'
  | 'newInviteOnHost'
  | 'nativePermission'
  | 'nativeRecovery'
  | 'operatorRecovery'
  | 'none';
export type Cleanup = 'confirmed' | 'incomplete' | 'unknown' | 'notApplicable';
export type ClientStage =
  | 'idle'
  | 'validating'
  | 'joining'
  | 'pairing'
  | 'waitingApproval'
  | 'saving'
  | 'opening'
  | 'connected'
  | 'stopping'
  | 'failed';
export type SavedAccess = 'none' | 'saved' | 'notSaved' | 'needsPermission' | 'removalIncomplete' | 'unknown';
export type Observation = 'known' | 'stale' | 'unavailable';
export type Runtime = 'available' | 'offline' | 'restarting' | 'unknown';
export type Route = 'direct' | 'relay' | 'unknown';
export type InviteState = 'none' | 'issued' | 'expired' | 'used' | 'unknown'
  | 'minting' | 'encoding' | 'pairing' | 'refused' | 'uncertain' | 'recovered' | 'cancelled';
export type HostSetup = 'off' | 'pending' | 'ready' | 'paused' | 'failed' | 'unknown'
  | 'prepared_no_receipt' | 'failed_uncertain' | 'partial_cleanup' | 'runtime_unavailable' | 'setup_unverified' | 'renewal_failed';

export interface SavedComputer {
  computerRef: string;
  label: string;
}

export interface ClientSnapshot {
  stage: ClientStage;
  runtime: Runtime;
  savedAccess: SavedAccess;
  route: Route;
  observedAt: string | null;
  observation: Observation;
  computers: SavedComputer[] | null;
  activeComputerRef: string | null;
  expiresAt: string | null;
  comparisonPhrase: string | null;
}

export interface HostDevice {
  label: string;
  comparisonPhrase: string | null;
}

export interface HostSnapshot {
  setup: HostSetup;
  runtime: Runtime;
  observation: Observation;
  devices: HostDevice[] | null;
  pending: HostDevice | null;
  selected: HostDevice | null;
  inviteState: InviteState;
  expiresAt: string | null;
}

export interface ActionResult {
  effect: Effect;
  hostDenial: Cleanup;
  hostCleanup: Cleanup;
  localCleanup: Cleanup;
  networkCleanup: Cleanup;
  inviteState: InviteState;
  expiresAt: string | null;
}

export interface Health {
  access: 'allowed' | 'accessDenied' | 'revokedConfirmed' | 'unknown';
  runtime: Runtime;
  route: Route;
  observedAt: string | null;
  observation: Observation;
}

export interface HostEvent {
  sequence: number;
  observedAt: string;
  snapshot: HostSnapshot;
}

/** A refusal as the dialog sees it (Main envelope; `side` only for `incompatible`). */
export interface ConnectionFailure {
  code: string;
  effect: Effect;
  recovery: Recovery;
  side?: 'host' | 'app' | 'unknown';
  category?: 'residue' | 'owner' | 'provenance' | 'custody' | 'inventory' | 'quiescence' | 'timeout' | 'unknown' | PreparationCategory;
}

export type PreparationCategory =
  | 'deb-layout-required' | 'issuer-state-uncertain' | 'account-shape' | 'account-receipt-uncertain'
  | 'foreign-unit' | 'foreign-custody' | 'publication-uncertain' | 'address-changed'
  | 'account-create-uncertain' | 'cleanup-uncertain' | 'materializer-uncertain' | 'custody-uncertain'
  | 'inventory' | 'no_address' | 'multiple_addresses' | 'dial_ip' | 'dial-ip'
  | 'occupied-port' | 'port-check-uncertain'
  | 'ready-restart-failed-turn-off' | 'activation-retry-later' | 'activation-prepared-retry';

export interface EnableFinished {
  operationId: string;
  outcome: 'failed' | 'completed';
  failure?: ConnectionFailure;
}

const PREPARATION_CATEGORIES: readonly string[] = [
  'deb-layout-required', 'issuer-state-uncertain', 'account-shape', 'account-receipt-uncertain',
  'foreign-unit', 'foreign-custody', 'publication-uncertain', 'address-changed',
  'account-create-uncertain', 'cleanup-uncertain', 'materializer-uncertain', 'custody-uncertain',
  'inventory', 'no_address', 'multiple_addresses', 'dial_ip', 'dial-ip', 'occupied-port', 'port-check-uncertain',
  'ready-restart-failed-turn-off', 'activation-retry-later', 'activation-prepared-retry',
];

type Obj = Record<string, unknown>;

const STAGES: readonly string[] = ['idle', 'validating', 'joining', 'pairing', 'waitingApproval', 'saving', 'opening', 'connected', 'stopping', 'failed'];
const SAVED: readonly string[] = ['none', 'saved', 'notSaved', 'needsPermission', 'removalIncomplete', 'unknown'];
const OBSERVATION: readonly string[] = ['known', 'stale', 'unavailable'];
const RUNTIME: readonly string[] = ['available', 'offline', 'restarting', 'unknown'];
const ROUTE: readonly string[] = ['direct', 'relay', 'unknown'];
const EFFECT: readonly string[] = ['none', 'confirmed', 'unknown'];
const CLEANUP: readonly string[] = ['confirmed', 'incomplete', 'unknown', 'notApplicable'];
const INVITE: readonly string[] = ['none', 'issued', 'expired', 'used', 'unknown', 'minting', 'encoding', 'pairing', 'refused', 'uncertain', 'recovered', 'cancelled'];
const SETUP: readonly string[] = ['off', 'pending', 'ready', 'paused', 'failed', 'unknown',
  'prepared_no_receipt', 'failed_uncertain', 'partial_cleanup', 'runtime_unavailable', 'setup_unverified', 'renewal_failed'];
const ACCESS: readonly string[] = ['allowed', 'accessDenied', 'revokedConfirmed', 'unknown'];
const RECOVERY: readonly string[] = ['refresh', 'explicitRetry', 'newInviteOnHost', 'nativePermission', 'nativeRecovery', 'operatorRecovery', 'none'];
const REF = /^[A-Za-z0-9_-]{1,64}$/;
// Labels are display text only; control and bidi-override characters are refused.
const LABEL = /^[^\u0000-\u001f\u007f-\u009f‪-‮⁦-⁩]*$/;
const UTC = /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{3})?Z$/;
const MAX_LIST = 32;

function closed(value: unknown, keys: readonly string[]): value is Obj {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return false;
  const own = Object.keys(value);
  return own.length === keys.length && keys.every((key) => own.includes(key));
}

const oneOf = (value: unknown, allowed: readonly string[]): boolean => typeof value === 'string' && allowed.includes(value);
const isLabel = (value: unknown): boolean => typeof value === 'string' && value.length <= 256 && LABEL.test(value);
const isTime = (value: unknown): boolean => value === null || (typeof value === 'string' && UTC.test(value) && Number.isFinite(Date.parse(value)));
const isPhrase = (value: unknown): boolean => value === null || isLabel(value);

function json(raw: unknown): unknown {
  if (typeof raw !== 'string' || raw.length > 32768) return undefined;
  try {
    return JSON.parse(raw);
  } catch {
    return undefined;
  }
}

function isComputer(value: unknown): value is SavedComputer {
  return closed(value, ['computerRef', 'label']) && typeof value.computerRef === 'string' && REF.test(value.computerRef) && isLabel(value.label);
}

function isComputers(value: unknown): boolean {
  return value === null || (Array.isArray(value) && value.length <= MAX_LIST && value.every(isComputer));
}

function isDevice(value: unknown): value is HostDevice {
  return closed(value, ['label', 'comparisonPhrase']) && isLabel(value.label) && isPhrase(value.comparisonPhrase);
}

function isClient(value: unknown): value is ClientSnapshot {
  if (!closed(value, ['stage', 'runtime', 'savedAccess', 'route', 'observedAt', 'observation', 'computers', 'activeComputerRef', 'expiresAt', 'comparisonPhrase'])) return false;
  return (
    oneOf(value.stage, STAGES) &&
    oneOf(value.runtime, RUNTIME) &&
    oneOf(value.savedAccess, SAVED) &&
    oneOf(value.route, ROUTE) &&
    isTime(value.observedAt) &&
    oneOf(value.observation, OBSERVATION) &&
    isComputers(value.computers) &&
    (value.activeComputerRef === null || (typeof value.activeComputerRef === 'string' && REF.test(value.activeComputerRef))) &&
    isTime(value.expiresAt) &&
    isPhrase(value.comparisonPhrase)
  );
}

function isHost(value: unknown): value is HostSnapshot {
  if (!closed(value, ['setup', 'runtime', 'observation', 'devices', 'pending', 'selected', 'inviteState', 'expiresAt'])) return false;
  return (
    oneOf(value.setup, SETUP) &&
    oneOf(value.runtime, RUNTIME) &&
    oneOf(value.observation, OBSERVATION) &&
    (value.devices === null || (Array.isArray(value.devices) && value.devices.length <= MAX_LIST && value.devices.every(isDevice))) &&
    (value.pending === null || isDevice(value.pending)) &&
    (value.selected === null || isDevice(value.selected)) &&
    oneOf(value.inviteState, INVITE) &&
    isTime(value.expiresAt)
  );
}

/** `client.status` result. */
export function parseClientSnapshot(raw: unknown): ClientSnapshot | null {
  const value = json(raw);
  return isClient(value) ? value : null;
}

/** `client.savedComputers` result: the list (null = unreadable) and its access state. */
export function parseSavedComputers(raw: unknown): { computers: SavedComputer[] | null; savedAccess: SavedAccess; observation: Observation } | null {
  const value = json(raw);
  if (!closed(value, ['computers', 'savedAccess', 'observation'])) return null;
  if (!isComputers(value.computers) || !oneOf(value.savedAccess, SAVED) || !oneOf(value.observation, OBSERVATION)) return null;
  return value as unknown as { computers: SavedComputer[] | null; savedAccess: SavedAccess; observation: Observation };
}

/** `host.status` / `host.nextDevice` result. */
export function parseHostSnapshot(raw: unknown): HostSnapshot | null {
  const value = json(raw);
  return isHost(value) ? value : null;
}

/** `host.stateChanged` event (v1 hostEvent). */
export function parseHostEvent(raw: unknown): HostEvent | null {
  const value = json(raw);
  if (!closed(value, ['version', 'sequence', 'observedAt', 'snapshot'])) return null;
  if (value.version !== 1 || typeof value.sequence !== 'number' || !Number.isSafeInteger(value.sequence) || value.sequence < 1) return null;
  if (typeof value.observedAt !== 'string' || !isTime(value.observedAt) || !isHost(value.snapshot)) return null;
  return { sequence: value.sequence, observedAt: value.observedAt, snapshot: value.snapshot };
}

/** v1 ActionResult (the embedded host/client snapshots are ignored: the dialog re-reads). */
export function parseActionResult(raw: unknown): ActionResult | null {
  const value = json(raw);
  if (!closed(value, ['effect', 'hostDenial', 'hostCleanup', 'localCleanup', 'networkCleanup', 'inviteState', 'expiresAt', 'host', 'client'])) return null;
  if (
    !oneOf(value.effect, EFFECT) ||
    !oneOf(value.hostDenial, CLEANUP) ||
    !oneOf(value.hostCleanup, CLEANUP) ||
    !oneOf(value.localCleanup, CLEANUP) ||
    !oneOf(value.networkCleanup, CLEANUP) ||
    !oneOf(value.inviteState, INVITE) ||
    !isTime(value.expiresAt) ||
    !(value.host === null || isHost(value.host)) ||
    !(value.client === null || isClient(value.client))
  ) {
    return null;
  }
  return {
    effect: value.effect as Effect,
    hostDenial: value.hostDenial as Cleanup,
    hostCleanup: value.hostCleanup as Cleanup,
    localCleanup: value.localCleanup as Cleanup,
    networkCleanup: value.networkCleanup as Cleanup,
    inviteState: value.inviteState as InviteState,
    expiresAt: value.expiresAt as string | null,
  };
}

/** Additive admission metadata is allowed only for this Enable invocation. */
export function parseEnableAdmission(raw: unknown): { result: ActionResult; operationId?: string } | null {
  const value = json(raw);
  const legacy = parseActionResult(raw);
  if (legacy) return { result: legacy };
  if (!value || typeof value !== 'object' || Array.isArray(value)) return null;
  const { operationId, ...rest } = value as Obj;
  if (typeof operationId !== 'string' || !/^[0-9a-f]{32}$/.test(operationId)) return null;
  if (rest.effect !== 'unknown' || rest.hostDenial !== 'unknown' || rest.hostCleanup !== 'unknown'
      || rest.localCleanup !== 'notApplicable' || rest.networkCleanup !== 'unknown'
      || rest.inviteState !== 'unknown' || rest.expiresAt !== null || rest.host !== null || rest.client !== null) return null;
  const result = parseActionResult(JSON.stringify(rest));
  return result ? { result, operationId } : null;
}

export function parseEnableFinished(raw: unknown): EnableFinished | null {
  if (typeof raw !== 'string' || new TextEncoder().encode(raw).length > 1024) return null;
  const value = json(raw);
  if (!value || typeof value !== 'object' || Array.isArray(value)) return null;
  const obj = value as Obj;
  if (obj.version !== 1 || obj.method !== 'Enable' || typeof obj.operationId !== 'string'
      || !/^[0-9a-f]{32}$/.test(obj.operationId)) return null;
  if (obj.outcome === 'completed' && closed(obj, ['version', 'operationId', 'method', 'outcome', 'result'])
      && closed(obj.result, ['code', 'effect', 'recovery']) && obj.result.code === 'enableCompleted'
      && obj.result.effect === 'unknown' && obj.result.recovery === 'refresh') {
    return { operationId: obj.operationId, outcome: 'completed' };
  }
  if (obj.outcome !== 'failed' || !closed(obj, ['version', 'operationId', 'method', 'outcome', 'error'])) return null;
  const failure = parseConnectionFailure(JSON.stringify(obj.error));
  if (failure.code === 'enablePreparationFailed'
      || (closed(obj.error, ['code', 'effect', 'recovery']) && obj.error.code === 'outcomeUnknown'
          && obj.error.effect === 'unknown' && obj.error.recovery === 'nativeRecovery')) {
    return { operationId: obj.operationId, outcome: 'failed', failure };
  }
  return null;
}

/** A component-lifetime listener: no replay, buffering or mutation. */
export function subscribeEnableFinished(handler: (event: EnableFinished) => void): (() => void) | null {
  const native = nativeBridge();
  if (!native) return null;
  let active = true;
  let off: unknown;
  try {
    off = native.on('host.enableFinished', (raw) => {
      if (!active) return;
      const event = parseEnableFinished(raw);
      if (event) handler(event);
    });
  } catch {
    return null;
  }
  return () => {
    active = false;
    if (typeof off === 'function') {
      try { (off as () => void)(); } catch { /* Already retired. */ }
    }
  };
}

/** `client.health` result. */
export function parseHealth(raw: unknown): Health | null {
  const value = json(raw);
  if (!closed(value, ['access', 'runtime', 'route', 'observedAt', 'observation'])) return null;
  if (!oneOf(value.access, ACCESS) || !oneOf(value.runtime, RUNTIME) || !oneOf(value.route, ROUTE) || !isTime(value.observedAt) || !oneOf(value.observation, OBSERVATION)) return null;
  return value as unknown as Health;
}

/** Closed fallback: an unreadable refusal is never "nothing changed". */
export const UNKNOWN_FAILURE: ConnectionFailure = { code: 'outcomeUnknown', effect: 'unknown', recovery: 'nativeRecovery' };

/** Parse a rejection (the Main envelope). Unknown shapes become `UNKNOWN_FAILURE`. */
export function parseConnectionFailure(error: unknown): ConnectionFailure {
  const value = json(error);
  if (!value || typeof value !== 'object' || Array.isArray(value)) return UNKNOWN_FAILURE;
  const obj = value as Obj;
  if (obj.code === 'enableCompleted') return UNKNOWN_FAILURE;
  if (obj.code === 'enablePreparationFailed') {
    if (!closed(obj, ['code', 'category', 'effect', 'recovery'])
        || !oneOf(obj.category, PREPARATION_CATEGORIES)
        || obj.effect !== 'unknown' || obj.recovery !== 'nativeRecovery') return UNKNOWN_FAILURE;
    return { code: 'enablePreparationFailed', category: obj.category as PreparationCategory,
      effect: 'unknown', recovery: 'nativeRecovery' };
  }
  const keys = Object.keys(obj);
  const allowed = ['code', 'effect', 'recovery', 'side', 'category'];
  if (!keys.every((key) => allowed.includes(key))) return UNKNOWN_FAILURE;
  if (typeof obj.code !== 'string' || !/^[A-Za-z_]{1,64}$/.test(obj.code)) return UNKNOWN_FAILURE;
  if (!oneOf(obj.effect, EFFECT) || !oneOf(obj.recovery, RECOVERY)) return UNKNOWN_FAILURE;
  const failure: ConnectionFailure = { code: obj.code, effect: obj.effect as Effect, recovery: obj.recovery as Recovery };
  if ('side' in obj) {
    if (obj.code !== 'incompatible' || !oneOf(obj.side, ['host', 'app', 'unknown'])) return UNKNOWN_FAILURE;
    failure.side = obj.side as 'host' | 'app' | 'unknown';
  }
  if ('category' in obj && obj.code !== 'hostStateUnrecognized') return UNKNOWN_FAILURE;
  if (obj.code === 'hostStateUnrecognized') {
    const categories = ['residue', 'owner', 'provenance', 'custody', 'inventory', 'quiescence', 'timeout', 'unknown'];
    failure.category = (oneOf(obj.category, categories) ? obj.category : 'unknown') as ConnectionFailure['category'];
  }
  return failure;
}

/** A refused native call. `failure` is the parsed envelope; the message stays empty. */
export class ConnectionRefusal extends Error {
  public override readonly name = 'ConnectionRefusal';
  constructor(public readonly failure: ConnectionFailure) {
    super('');
  }
}

/** The failure carried by a rejection from `callConnection` (closed fallback otherwise). */
export function failureOf(error: unknown): ConnectionFailure {
  return error instanceof ConnectionRefusal ? error.failure : UNKNOWN_FAILURE;
}

/** Reads (no native effect); every other command may change native state. */
export function isReadCommand(command: ConnectionCommand): boolean {
  return command === 'client.status' || command === 'client.savedComputers' || command === 'client.health' || command === 'host.status' || command === 'host.nextDevice';
}

/**
 * Call one admitted command. Resolves with native's raw result string; rejects
 * with a `ConnectionRefusal` (see `failureOf`). Without a bridge (browser mode) every call is
 * refused `unsupportedCommand` with no effect.
 */
export async function callConnection(command: ConnectionCommand, args: Record<string, string> = {}): Promise<string> {
  const native = nativeBridge();
  if (!native) throw new ConnectionRefusal({ code: 'unsupportedCommand', effect: 'none', recovery: 'none' });
  const data = JSON.stringify(args);
  try {
    return await native.call(command, data);
  } catch (error) {
    throw new ConnectionRefusal(parseConnectionFailure(error));
  }
}

/**
 * Subscribe to `host.stateChanged`. Events are delivered in sequence order
 * only (an older or repeated sequence is ignored). A bridge without the event
 * (its `on` throws, e.g. Mac without a host role) returns null.
 */
export function subscribeHostState(handler: (event: HostEvent) => void): (() => void) | null {
  const native = nativeBridge();
  if (!native) return null;
  let last = 0;
  let off: unknown;
  try {
    off = native.on('host.stateChanged', (raw) => {
      const event = parseHostEvent(raw);
      if (!event || event.sequence <= last) return;
      last = event.sequence;
      handler(event);
    });
  } catch {
    return null;
  }
  return () => {
    if (typeof off !== 'function') return;
    try {
      (off as () => void)();
    } catch {
      // Already retired subscription.
    }
  };
}
