/**
 * Native bridge v2 `appState` (THR-387 S5-W1).
 *
 * The desktop app answers `app.status` and pushes `app.stateChanged` with the
 * JSON value described by `protocol/native-connection-bridge.v2.json`
 * `#/$defs/appState` (spec docs/specs/2026-11-17-web-native-bridge.md §12).
 * This module only parses that closed shape and derives presentation state;
 * it never invents a generation, address or credential.
 *
 * Pure module (no imports).
 */

export type AppStateCode = 'no_target' | 'target_starting' | 'ready' | 'unreachable' | 'incompatible' | 'busy';

export interface AppTarget {
  kind: 'local' | 'remote';
  id: string;
  name: string;
}

export interface AppIncompatible {
  side: 'host' | 'app' | 'unknown';
  host_contract: { current: number; min_client: number } | null;
  client_contract: 1;
  min_daemon: 1;
}

export interface AppLocal {
  supported: boolean;
  mode: 'service' | 'foreground' | 'unsupported';
  running: 'running' | 'starting' | 'stopped' | 'unknown';
  prompt_base_url: string | null;
}

export interface AppCandidate {
  kind: 'local' | 'remote';
  id: string;
  name: string;
  phase: 'signing_in' | 'awaiting_approval' | 'ready_to_switch' | 'failed' | 'cancelled';
  deadline: string | null;
}

export interface AppState {
  schema: 2;
  generation: string;
  target: AppTarget | null;
  state: AppStateCode;
  incompatible: AppIncompatible | null;
  local: AppLocal;
  candidate: AppCandidate | null;
  access_unsaved: boolean;
  locale: { current: string; persisted: boolean };
}

const STATES: readonly string[] = ['no_target', 'target_starting', 'ready', 'unreachable', 'incompatible', 'busy'];

type Obj = Record<string, unknown>;

function closed(value: unknown, keys: readonly string[]): value is Obj {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return false;
  const own = Object.keys(value);
  return own.length === keys.length && keys.every((k) => own.includes(k));
}

const oneOf = (value: unknown, allowed: readonly string[]): boolean =>
  typeof value === 'string' && allowed.includes(value);

const isInt = (value: unknown): boolean => typeof value === 'number' && Number.isInteger(value);

/** Generation: nonempty ASCII, at most 32 characters (compared for equality only). */
export function isGeneration(value: unknown): value is string {
  return typeof value === 'string' && value.length >= 1 && value.length <= 32 && /^[\x00-\x7f]+$/.test(value);
}

function isTarget(value: unknown): value is AppTarget {
  if (!closed(value, ['kind', 'id', 'name'])) return false;
  if (!oneOf(value.kind, ['local', 'remote']) || typeof value.id !== 'string' || typeof value.name !== 'string') return false;
  return value.kind !== 'local' || value.id === 'local';
}

function isIncompatible(value: unknown): value is AppIncompatible {
  if (!closed(value, ['side', 'host_contract', 'client_contract', 'min_daemon'])) return false;
  if (!oneOf(value.side, ['host', 'app', 'unknown'])) return false;
  if (value.client_contract !== 1 || value.min_daemon !== 1) return false;
  const host = value.host_contract;
  if (host === null) return value.side === 'host';
  return closed(host, ['current', 'min_client']) && isInt(host.current) && isInt(host.min_client);
}

function isLocal(value: unknown): value is AppLocal {
  if (!closed(value, ['supported', 'mode', 'running', 'prompt_base_url'])) return false;
  return (
    typeof value.supported === 'boolean' &&
    oneOf(value.mode, ['service', 'foreground', 'unsupported']) &&
    oneOf(value.running, ['running', 'starting', 'stopped', 'unknown']) &&
    (value.prompt_base_url === null || typeof value.prompt_base_url === 'string')
  );
}

function isCandidate(value: unknown): value is AppCandidate {
  if (!closed(value, ['kind', 'id', 'name', 'phase', 'deadline'])) return false;
  return (
    oneOf(value.kind, ['local', 'remote']) &&
    typeof value.id === 'string' &&
    typeof value.name === 'string' &&
    oneOf(value.phase, ['signing_in', 'awaiting_approval', 'ready_to_switch', 'failed', 'cancelled']) &&
    (value.deadline === null || typeof value.deadline === 'string')
  );
}

/**
 * Parse the string returned by `app.status` / pushed by `app.stateChanged`.
 * Returns null for anything that is not exactly the closed v2 shape, so the
 * caller keeps its previous state instead of guessing.
 */
export function parseAppState(raw: unknown): AppState | null {
  let value: unknown;
  try {
    value = typeof raw === 'string' ? JSON.parse(raw) : raw;
  } catch {
    return null;
  }
  if (!closed(value, ['schema', 'generation', 'target', 'state', 'incompatible', 'local', 'candidate', 'access_unsaved', 'locale'])) {
    return null;
  }
  if (value.schema !== 2 || !isGeneration(value.generation) || !oneOf(value.state, STATES)) return null;
  const noTarget = value.state === 'no_target';
  if (noTarget ? value.target !== null : !isTarget(value.target)) return null;
  const incompatible = value.state === 'incompatible';
  if (incompatible ? !isIncompatible(value.incompatible) : value.incompatible !== null) return null;
  if (!isLocal(value.local)) return null;
  // A remote target never discloses an address: prompt_base_url must be null.
  if ((value.target as AppTarget | null)?.kind === 'remote' && value.local.prompt_base_url !== null) return null;
  if (value.candidate !== null && !isCandidate(value.candidate)) return null;
  if (typeof value.access_unsaved !== 'boolean') return null;
  const locale = value.locale;
  if (!closed(locale, ['current', 'persisted']) || typeof locale.current !== 'string' || locale.current === '' || typeof locale.persisted !== 'boolean') {
    return null;
  }
  return value as unknown as AppState;
}

/**
 * Display name for a target or candidate (THR-387 S8-1-W, bridge Q5). Native
 * names the built-in local computer in its launch language, so the web uses
 * its own live-locale `localName` where the semantic `kind` is `local`; an
 * authored remote name stays verbatim. Display strings are never matched.
 */
export function appEntryName(entry: { kind: 'local' | 'remote'; name: string }, localName: string): string {
  return entry.kind === 'local' ? localName : entry.name;
}

/**
 * Connection/settings menu state derived from `app.status` (S5-W1 item 7).
 * Presentation only: the S6 Connection/Settings dialogs consume it. Names are
 * display text; there is no address, invite or credential here.
 */
export interface ConnectionMenuState {
  /** Active target display, or null when nothing is selected. */
  target: { kind: 'local' | 'remote'; name: string } | null;
  state: AppStateCode;
  /** True only when the active target can serve requests. */
  ready: boolean;
  /** Local service controls apply only while the local computer is active. */
  localActive: boolean;
  localRunning: AppLocal['running'];
  localSupported: boolean;
  /** Persistent "Access wasn't saved" badge. */
  accessUnsaved: boolean;
  /** A pending/finished connection attempt separate from the active target. */
  candidate: { kind: 'local' | 'remote'; name: string; phase: AppCandidate['phase'] } | null;
  incompatibleSide: AppIncompatible['side'] | null;
}

export function deriveConnectionMenuState(app: AppState): ConnectionMenuState {
  return {
    target: app.target ? { kind: app.target.kind, name: app.target.name } : null,
    state: app.state,
    ready: app.state === 'ready',
    localActive: app.target?.kind === 'local',
    localRunning: app.local.running,
    localSupported: app.local.supported,
    accessUnsaved: app.access_unsaved,
    candidate: app.candidate ? { kind: app.candidate.kind, name: app.candidate.name, phase: app.candidate.phase } : null,
    incompatibleSide: app.incompatible?.side ?? null,
  };
}
