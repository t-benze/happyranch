/**
 * Typed native `local.*` commands for Settings → HappyRanch on this computer
 * (THR-387 S7-L2; handoff v2.4 §6, C5/C6).
 *
 * Linux Main admits exactly eight closed commands, routed to the app's one
 * existing ServiceMode supervisor (packaging/linux/desktop
 * `LocalControls` / `AppScheme.bridge_message`):
 *
 * - reads: `local.status` → `{schema:2, service}`, `local.update.status` →
 *   `{schema:2, update}`;
 * - mutations: `local.start`, `local.stop`, `local.restart`, `local.repair`,
 *   `local.turnOff` (data `{}`) and `local.keepRunning.set`
 *   (`{schema:2, enabled}`) → `{schema:2, service, command}`.
 *
 * Refusals are the Main envelope `{code, effect, recovery}` (busy, unavailable,
 * permissionDenied, documentChanged, outcomeUnknown, owner/effect gate codes).
 * Native owns consent, the single mutation slot and reconciliation: this
 * module never retries, replays or guesses. Anything that is not exactly the
 * closed shape is `null` (results) or the unknown-effect fallback (refusals).
 *
 * Pure apart from the bridge accessor (no React/DOM).
 */
import { nativeBridge } from '@/lib/native/target';

export type LocalRunning = 'running' | 'starting' | 'stopped' | 'unknown';
export type KeepRunning = 'on' | 'off' | 'partial' | 'checking' | 'unknown';
export type LocalMode = 'service' | 'foreground' | 'unsupported';
export type UpdateStatus = 'restart_required' | 'not_detected' | 'unknown' | 'unsupported';

export interface LocalServiceSnapshot {
  supported: boolean;
  mode: LocalMode;
  running: LocalRunning;
  keepRunning: KeepRunning;
  busy: boolean;
}

export type LocalMutation =
  | 'local.start'
  | 'local.stop'
  | 'local.restart'
  | 'local.repair'
  | 'local.turnOff'
  | 'local.keepRunning.set';

export type LocalCommand = LocalMutation | 'local.status' | 'local.update.status';

export type LocalEffect = 'none' | 'confirmed' | 'unknown';

export interface LocalFailure {
  code: string;
  effect: LocalEffect;
  recovery: string;
}

/** Closed fallback: an unreadable refusal is never "nothing changed". */
export const LOCAL_UNKNOWN_FAILURE: LocalFailure = { code: 'outcomeUnknown', effect: 'unknown', recovery: 'nativeRecovery' };

export class LocalRefusal extends Error {
  public override readonly name = 'LocalRefusal';
  constructor(public readonly failure: LocalFailure) {
    super('');
  }
}

type Obj = Record<string, unknown>;

const RUNNING: readonly string[] = ['running', 'starting', 'stopped', 'unknown'];
const KEEP: readonly string[] = ['on', 'off', 'partial', 'checking', 'unknown'];
const MODES: readonly string[] = ['service', 'foreground', 'unsupported'];
const UPDATES: readonly string[] = ['restart_required', 'not_detected', 'unknown', 'unsupported'];
const EFFECTS: readonly string[] = ['none', 'confirmed', 'unknown'];
const RECOVERIES: readonly string[] = ['refresh', 'explicitRetry', 'newInviteOnHost', 'nativePermission', 'nativeRecovery', 'operatorRecovery', 'none'];
const MUTATIONS: readonly string[] = ['local.start', 'local.stop', 'local.restart', 'local.repair', 'local.turnOff', 'local.keepRunning.set'];

function closed(value: unknown, keys: readonly string[]): value is Obj {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return false;
  const own = Object.keys(value);
  return own.length === keys.length && keys.every((key) => own.includes(key));
}

const oneOf = (value: unknown, allowed: readonly string[]): boolean => typeof value === 'string' && allowed.includes(value);

function json(raw: unknown): unknown {
  if (typeof raw !== 'string' || raw.length > 4096) return undefined;
  try {
    return JSON.parse(raw);
  } catch {
    return undefined;
  }
}

function isSnapshot(value: unknown): value is LocalServiceSnapshot {
  return (
    closed(value, ['supported', 'mode', 'running', 'keepRunning', 'busy']) &&
    typeof value.supported === 'boolean' &&
    oneOf(value.mode, MODES) &&
    oneOf(value.running, RUNNING) &&
    oneOf(value.keepRunning, KEEP) &&
    typeof value.busy === 'boolean'
  );
}

/** `local.status` result, or a mutation result (which also names its command). */
export function parseLocalServiceResult(raw: unknown, command?: LocalMutation): LocalServiceSnapshot | null {
  const value = json(raw);
  if (command) {
    if (!closed(value, ['schema', 'service', 'command']) || value.command !== command) return null;
  } else if (!closed(value, ['schema', 'service'])) {
    return null;
  }
  return value.schema === 2 && isSnapshot(value.service) ? value.service : null;
}

/** `local.update.status` result. */
export function parseUpdateStatus(raw: unknown): UpdateStatus | null {
  const value = json(raw);
  if (!closed(value, ['schema', 'update']) || value.schema !== 2 || !oneOf(value.update, UPDATES)) return null;
  return value.update as UpdateStatus;
}

/** Parse a rejection (Main envelope). Unknown shapes become the unknown-effect fallback. */
export function parseLocalFailure(error: unknown): LocalFailure {
  const value = json(error);
  if (!closed(value, ['code', 'effect', 'recovery'])) return LOCAL_UNKNOWN_FAILURE;
  if (typeof value.code !== 'string' || !/^[A-Za-z_]{1,64}$/.test(value.code)) return LOCAL_UNKNOWN_FAILURE;
  if (!oneOf(value.effect, EFFECTS) || !oneOf(value.recovery, RECOVERIES)) return LOCAL_UNKNOWN_FAILURE;
  return { code: value.code, effect: value.effect as LocalEffect, recovery: value.recovery as string };
}

export function localFailureOf(error: unknown): LocalFailure {
  return error instanceof LocalRefusal ? error.failure : LOCAL_UNKNOWN_FAILURE;
}

export function isLocalMutation(command: string): command is LocalMutation {
  return MUTATIONS.includes(command);
}

/**
 * Call one admitted local command with its exact closed data string. Without
 * a bridge (browser mode) every call is refused `unsupportedCommand`, no effect.
 */
export async function callLocal(command: LocalCommand, args?: { enabled: boolean }): Promise<string> {
  const native = nativeBridge();
  if (!native) throw new LocalRefusal({ code: 'unsupportedCommand', effect: 'none', recovery: 'none' });
  const data = command === 'local.keepRunning.set' ? JSON.stringify({ schema: 2, enabled: args?.enabled === true }) : '{}';
  try {
    return await native.call(command, data);
  } catch (error) {
    throw new LocalRefusal(parseLocalFailure(error));
  }
}
