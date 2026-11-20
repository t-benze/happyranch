/**
 * Native recovery states (THR-387 S1b).
 *
 * In the desktop app the native shell answers API/stream requests it does not
 * forward with exactly `{"detail": {"code": C}}`:
 *   - status 503 for C in no_target, target_starting, target_unreachable,
 *     incompatible, busy (incompatible may add `detail.side` =
 *     "host" | "app" | "unknown");
 *   - status 409 for C = target_changed.
 *
 * These are connection/target states, never authentication failures: nothing
 * here clears the bearer or logs out. Any other 503/409 body (including daemon
 * errors that share a status) is not recognized and keeps today's behavior.
 * The daemon itself emits none of these codes, so daemon-served browser mode is
 * unchanged unless this exact shape appears.
 *
 * Pure module (no imports) holding one app-wide external store; React reads it
 * through `@/hooks/nativeRecovery`.
 */

export const NATIVE_RECOVERY_503_CODES = [
  'no_target',
  'target_starting',
  'target_unreachable',
  'incompatible',
  'busy',
] as const;

export type NativeRecovery503Code = (typeof NATIVE_RECOVERY_503_CODES)[number];
export type NativeRecoveryCode = NativeRecovery503Code | 'target_changed';
export type IncompatibleSide = 'host' | 'app' | 'unknown';

export type NativeRecoveryState =
  | { code: Exclude<NativeRecoveryCode, 'incompatible'> }
  | { code: 'incompatible'; side: IncompatibleSide };

const SIDES: readonly string[] = ['host', 'app', 'unknown'];

function isPlainObject(value: unknown): value is Record<string, unknown> {
  return !!value && typeof value === 'object' && !Array.isArray(value);
}

/**
 * Recognize only the exact native contract shape. Returns null for anything
 * else so callers fall through to their existing error handling.
 *
 * The body must be an object whose only key is `detail`; `detail` must be an
 * object whose only key is `code` (plus optional `side` for incompatible).
 * An incompatible `side` outside host/app/unknown is treated as unknown
 * (compatibility unavailable: no guessed component to update).
 */
export function parseNativeRecovery(status: number, body: unknown): NativeRecoveryState | null {
  if (status !== 503 && status !== 409) return null;
  if (!isPlainObject(body)) return null;
  const bodyKeys = Object.keys(body);
  if (bodyKeys.length !== 1 || bodyKeys[0] !== 'detail') return null;
  const detail = body.detail;
  if (!isPlainObject(detail)) return null;
  const code = detail.code;
  if (typeof code !== 'string') return null;
  const extra = Object.keys(detail).filter((k) => k !== 'code');

  if (status === 409) {
    return code === 'target_changed' && extra.length === 0 ? { code: 'target_changed' } : null;
  }
  if (!(NATIVE_RECOVERY_503_CODES as readonly string[]).includes(code)) return null;
  if (code === 'incompatible') {
    if (extra.some((k) => k !== 'side')) return null;
    const side = detail.side;
    return {
      code: 'incompatible',
      side: typeof side === 'string' && SIDES.includes(side) ? (side as IncompatibleSide) : 'unknown',
    };
  }
  if (extra.length !== 0) return null;
  return { code: code as Exclude<NativeRecovery503Code, 'incompatible'> };
}

/** Parse a raw response body text (JSON or not) against the contract. */
export function parseNativeRecoveryText(status: number, text: string): NativeRecoveryState | null {
  if (status !== 503 && status !== 409) return null;
  try {
    return parseNativeRecovery(status, JSON.parse(text));
  } catch {
    return null;
  }
}

// ---------------------------------------------------------------------------
// App-wide store
// ---------------------------------------------------------------------------

let current: NativeRecoveryState | null = null;
const listeners = new Set<() => void>();

function sameState(a: NativeRecoveryState | null, b: NativeRecoveryState | null): boolean {
  if (a === null || b === null) return a === b;
  if (a.code !== b.code) return false;
  return a.code !== 'incompatible' || a.side === (b as { side: IncompatibleSide }).side;
}

function publish(next: NativeRecoveryState | null): void {
  current = next;
  for (const listener of [...listeners]) listener();
}

/** Record the latest observed recovery state (latest observation wins). */
export function reportNativeRecovery(state: NativeRecoveryState): void {
  if (sameState(current, state)) return;
  publish(state);
}

/**
 * Clear the active state before an explicit Retry/Recheck. A request that
 * still meets the native answer reports it again.
 */
export function clearNativeRecovery(): void {
  if (current === null) return;
  publish(null);
}

const retryListeners = new Set<() => void>();

/**
 * Explicit Retry/Recheck: clear the active state, then tell read owners that
 * keep their own latched failure (outside TanStack refetch, e.g. the Threads
 * committed-page owner) to retry. A request that still meets the native answer
 * reports the state again.
 */
export function retryNativeRecovery(): void {
  clearNativeRecovery();
  for (const listener of [...retryListeners]) listener();
}

/** Subscribe a latched read owner to explicit Retry/Recheck. */
export function subscribeNativeRecoveryRetry(listener: () => void): () => void {
  retryListeners.add(listener);
  return () => {
    retryListeners.delete(listener);
  };
}

/** Current recovery state, or null when none is active (stable reference). */
export function getNativeRecoveryState(): NativeRecoveryState | null {
  return current;
}

export function subscribeNativeRecovery(listener: () => void): () => void {
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
}

/** Test-only: reset the module store. */
export function __resetNativeRecoveryForTests(): void {
  current = null;
  listeners.clear();
  retryListeners.clear();
}
