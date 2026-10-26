/**
 * Daemon startup admission for the web client (THR-308).
 *
 * The daemon reports its startup gate in the `startup` block of `/health`.
 * Liveness (HTTP 200) never means recovered: only a fresh, successful, valid
 * observation with `state=ready`, `recovery_ready`, `serving` and not
 * `stopping` admits new effects or held reads. Everything else — still
 * checking, request error, malformed or missing fields, an unknown state,
 * stopping, or an observation older than `STARTUP_MAX_AGE_MS` — is unavailable.
 *
 * There is exactly one observation: the shared `['health']` query, polled by a
 * single owner (the app shell). This module only interprets that query state;
 * it never fetches, retries or invents a boot identity the daemon did not send.
 */
import type { QueryClient } from '@tanstack/react-query';
import { ApiError } from './api/client';
import type { HealthResponse, StartupStatus } from './api/types';

export const HEALTH_QUERY_KEY = ['health'] as const;
/** Bounded shared poll while unavailable (matches the daemon's Retry-After). */
export const STARTUP_POLL_HELD_MS = 2_000;
/** Ordinary liveness cadence once positively ready. */
export const STARTUP_POLL_READY_MS = 30_000;
/** A ready observation older than three ready polls is no longer fresh. */
export const STARTUP_MAX_AGE_MS = 90_000;

export type StartupPhase = 'checking' | 'ready' | 'recovering' | 'failed' | 'stopping' | 'unknown';

export interface StartupView {
  phase: StartupPhase;
  /** True only for a fresh positive ready observation. */
  admitted: boolean;
  /** Stable daemon classification (never raw cause text); null when ready/unknown. */
  errorCode: string | null;
}

/** Rendered view plus an operation-time recheck for use after awaits. */
export interface StartupAdmission extends StartupView {
  isAdmittedNow: () => boolean;
  /** Throws StartupHeldError (with the current phase) unless ready now. */
  assertAdmittedNow: () => void;
}

/** The minimal query-state shape both the cache and a query observer expose. */
export interface HealthObservation {
  status: 'pending' | 'error' | 'success';
  data: HealthResponse | undefined;
  dataUpdatedAt: number;
}

const STATES = new Set<StartupStatus['state']>(['recovering', 'ready', 'recovery_failed', 'unknown', 'stopped']);
const STABLE_CODE = /^[a-z][a-z0-9_]{0,63}$/;

/** Strictly validate the public startup block; anything else is unknown. */
export function parseStartupStatus(value: unknown): StartupStatus | null {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return null;
  const s = value as Record<string, unknown>;
  if (typeof s.state !== 'string' || !STATES.has(s.state as StartupStatus['state'])) return null;
  if (typeof s.serving !== 'boolean' || typeof s.recovery_ready !== 'boolean' || typeof s.stopping !== 'boolean') return null;
  if (s.error_code !== null && typeof s.error_code !== 'string') return null;
  return s as unknown as StartupStatus;
}

function stableCode(code: string | null): string | null {
  return code !== null && STABLE_CODE.test(code) ? code : null;
}

const UNKNOWN: StartupView = { phase: 'unknown', admitted: false, errorCode: null };

export function deriveStartupView(observation: HealthObservation | undefined, now = Date.now()): StartupView {
  if (!observation || (observation.status === 'pending' && observation.data === undefined)) {
    return { phase: 'checking', admitted: false, errorCode: null };
  }
  // A failed latest observation is unknown even when an older body is cached.
  if (observation.status === 'error') return UNKNOWN;
  const startup = parseStartupStatus(observation.data?.startup);
  if (!startup) return UNKNOWN;
  const code = stableCode(startup.error_code);
  if (startup.stopping || startup.state === 'stopped') return { phase: 'stopping', admitted: false, errorCode: code };
  if (startup.state === 'recovery_failed') return { phase: 'failed', admitted: false, errorCode: code };
  if (startup.state === 'recovering') return { phase: 'recovering', admitted: false, errorCode: code };
  if (startup.state === 'ready' && startup.recovery_ready && startup.serving) {
    if (now - observation.dataUpdatedAt > STARTUP_MAX_AGE_MS) return UNKNOWN;
    return { phase: 'ready', admitted: true, errorCode: null };
  }
  return { phase: 'unknown', admitted: false, errorCode: code };
}

/** Current admission from the shared observation, read at operation time. */
export function readStartupView(qc: QueryClient): StartupView {
  const state = qc.getQueryState<HealthResponse>(HEALTH_QUERY_KEY);
  return deriveStartupView(state ? { status: state.status, data: state.data, dataUpdatedAt: state.dataUpdatedAt } : undefined);
}

export function isStartupAdmittedIn(qc: QueryClient): boolean {
  return readStartupView(qc).admitted;
}

/** Client-side refusal raised before any network work while startup is unavailable. */
export class StartupHeldError extends Error {
  public override readonly name = 'StartupHeldError';
  public readonly code = 'startup_held';
  constructor(public readonly phase: StartupPhase = 'unknown') {
    super('startup_held');
  }
}

/** Throw before an effect or held read unless startup is positively ready now. */
export function assertStartupAdmitted(qc: QueryClient): void {
  const view = readStartupView(qc);
  if (!view.admitted) throw new StartupHeldError(view.phase);
}

const STARTUP_CODES = new Set(['daemon_recovering', 'daemon_recovery_failed', 'daemon_stopped', 'startup_gate_unbound']);

/** True for an actionable startup refusal: the request was NOT accepted. */
export function isStartupUnavailableError(error: unknown): boolean {
  if (error instanceof StartupHeldError) return true;
  return error instanceof ApiError && error.status === 503 && !!error.code
    && (STARTUP_CODES.has(error.code) || error.code.startsWith('startup_reader_'));
}
