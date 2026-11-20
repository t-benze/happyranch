/**
 * Non-blocking native request notices (THR-387 S5-W1; W-1).
 *
 * Unlike the page-level recovery states in `lib/nativeRecovery.ts`, these
 * never replace the page:
 *   - `busy`: host busy (503 busy). Reads retry with backoff; the notice
 *     clears on the next successful forwarded response (W-1).
 *   - `throttled`: native per-view cap (429 too_many_requests). Reads retry
 *     with backoff; clears on the next successful forwarded response.
 *   - `not_sent`: a change was refused before it was forwarded (409
 *     target_changed). It was not sent and is never retried automatically;
 *     the draft stays where it is.
 *   - `result_unknown`: a forwarded change was interrupted by a target switch
 *     (499 cancelled / stream error). It may or may not have been applied and
 *     is never retried automatically.
 * The two outcome notices stay until dismissed; a transient notice never
 * replaces an outcome notice.
 *
 * Pure module (no imports).
 */

export type NativeNoticeKind = 'busy' | 'throttled' | 'not_sent' | 'result_unknown';

const TRANSIENT: readonly NativeNoticeKind[] = ['busy', 'throttled'];

let current: NativeNoticeKind | null = null;
const listeners = new Set<() => void>();

function publish(next: NativeNoticeKind | null): void {
  if (next === current) return;
  current = next;
  for (const listener of [...listeners]) listener();
}

export function isTransientNotice(kind: NativeNoticeKind | null): boolean {
  return kind !== null && TRANSIENT.includes(kind);
}

/** Record a notice. Transient notices never replace an outcome notice. */
export function reportNativeNotice(kind: NativeNoticeKind): void {
  if (isTransientNotice(kind) && current !== null && !isTransientNotice(current)) return;
  publish(kind);
}

const recoveryListeners = new Set<() => void>();

/**
 * A successful forwarded response or a native `ready` status clears
 * busy/throttled (not outcomes) and signals transient recovery, so failed
 * reads can be refetched (F3). The signal fires even when an outcome notice
 * hides the transient one.
 */
export function clearTransientNativeNotice(): void {
  if (isTransientNotice(current)) publish(null);
  for (const listener of [...recoveryListeners]) listener();
}

/** Subscribe to transient recovery (see clearTransientNativeNotice). */
export function subscribeTransientRecovery(listener: () => void): () => void {
  recoveryListeners.add(listener);
  return () => {
    recoveryListeners.delete(listener);
  };
}

/** Explicit user dismissal. */
export function dismissNativeNotice(): void {
  publish(null);
}

export function getNativeNotice(): NativeNoticeKind | null {
  return current;
}

export function subscribeNativeNotice(listener: () => void): () => void {
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
}

/** Test-only. */
export function __resetNativeNoticeForTests(): void {
  current = null;
  listeners.clear();
  recoveryListeners.clear();
}
