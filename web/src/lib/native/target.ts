/**
 * Native target generation (THR-387 S5-W1; spec §12 "v2 generation admission
 * and commit ordering").
 *
 * Desktop (Linux app scheme, `happyranch-app://app` with the
 * `window.happyranchConnection` facade): the generation is seeded from
 * `app.status` and updated from `app.stateChanged`. Every scheme API request
 * captures it at start and echoes it verbatim in `X-HappyRanch-Target`
 * (except `GET /api/v1/auth/bootstrap`). The web never invents or derives a
 * value: until native has reported one, requests wait for it.
 *
 * Browser (`happyranch web`, Mac daemon-served pages): no bridge, the
 * generation is the constant `browser`, no header is sent and none of the
 * change logic runs.
 *
 * On a change, listeners (query client, SSE) run first, then latched read
 * owners are retried, then the recovery store is synced. The page-level
 * recovery store follows the native status (latest wins):
 * ready clears it, no_target/target_starting/unreachable/incompatible set it,
 * and host busy is a non-blocking notice (W-1), never a page state.
 */
import { clearNativeRecovery, reportNativeRecovery, retryNativeRecovery } from '../nativeRecovery';
import { parseAppState, type AppState } from './appStatus';
import { clearTransientNativeNotice, reportNativeNotice } from './notices';

export const BROWSER_GENERATION = 'browser';
export const TARGET_HEADER = 'X-HappyRanch-Target';
const APP_ORIGIN = 'happyranch-app://app';
/** Bound for a native call's reply (`app.status`, `app.locale.set`). */
export const STATUS_TIMEOUT_MS = 5000;

export interface NativeBridge {
  call(command: string, data: string): Promise<string>;
  on(event: string, handler: (raw: string) => void): () => void;
}

/** Native has not reported a usable generation yet (nothing was sent). */
export class TargetUnknownError extends Error {
  public override readonly name = 'TargetUnknownError';
  constructor() {
    super('native target generation unavailable');
  }
}

let bridge: NativeBridge | null | undefined;
let status: AppState | null = null;
/** Generation of the last switch; null until the first change (see key hash). */
let keyGeneration: string | null = null;
let subscribed = false;
let unsubscribe: (() => void) | null = null;
let seeding: Promise<AppState | null> | null = null;
let eventCount = 0;
const statusListeners = new Set<() => void>();
const changeListeners = new Set<(previous: string, next: string) => void>();

function detectBridge(): NativeBridge | null {
  const scope = globalThis as unknown as {
    location?: { origin?: string };
    happyranchConnection?: Partial<NativeBridge>;
  };
  if (scope.location?.origin !== APP_ORIGIN) return null;
  const facade = scope.happyranchConnection;
  if (!facade || typeof facade.call !== 'function' || typeof facade.on !== 'function') return null;
  return facade as NativeBridge;
}

function getBridge(): NativeBridge | null {
  if (bridge === undefined) bridge = detectBridge();
  return bridge;
}

/** The detected native v2 bridge, or null in browser mode (read-only accessor). */
export function nativeBridge(): NativeBridge | null {
  return getBridge();
}

/** True when the page runs behind the native v2 bridge (desktop app). */
export function isNativeTarget(): boolean {
  return getBridge() !== null;
}

/** Current generation: `browser` without a bridge, null while unknown. */
export function currentGeneration(): string | null {
  if (!isNativeTarget()) return BROWSER_GENERATION;
  return status?.generation ?? null;
}

/** Latest native status (desktop only). */
export function getAppStatus(): AppState | null {
  return status;
}

/**
 * Base URL (`…/api/v1`) a copy-paste CLI prompt may use. Browser: the page's
 * own origin. Desktop: only native's verified local `prompt_base_url`; null
 * while unknown or when the target is remote (never a remote address).
 */
export function getPromptBaseUrl(): string | null {
  if (!isNativeTarget()) {
    const origin = (globalThis as { location?: { origin?: string } }).location?.origin;
    return origin ? `${origin}/api/v1` : null;
  }
  return status?.local.prompt_base_url ?? null;
}

/** Hash prefix for desktop query keys: the generation of the last switch. */
export function queryKeyGeneration(): string | null {
  return keyGeneration;
}

function syncRecovery(next: AppState): void {
  switch (next.state) {
    case 'ready':
      clearNativeRecovery();
      clearTransientNativeNotice();
      return;
    case 'busy':
      // W-1: host busy is transient and never page-blocking.
      clearNativeRecovery();
      reportNativeNotice('busy');
      return;
    case 'no_target':
      reportNativeRecovery({ code: 'no_target' });
      return;
    case 'target_starting':
      reportNativeRecovery({ code: 'target_starting' });
      return;
    case 'unreachable':
      reportNativeRecovery({ code: 'target_unreachable' });
      return;
    case 'incompatible':
      reportNativeRecovery({ code: 'incompatible', side: next.incompatible?.side ?? 'unknown' });
      return;
  }
}

function applyStatus(next: AppState): void {
  const previous = status;
  status = next;
  if (previous && previous.generation !== next.generation) {
    keyGeneration = next.generation;
    for (const listener of [...changeListeners]) listener(previous.generation, next.generation);
    // Latched read owners outside TanStack (Threads committed pages) retry
    // under the new generation, exactly as for an explicit Retry.
    retryNativeRecovery();
  }
  syncRecovery(next);
  for (const listener of [...statusListeners]) listener();
}

function withTimeout<T>(promise: Promise<T>): Promise<T> {
  return new Promise((resolve, reject) => {
    const timer = setTimeout(() => reject(new TargetUnknownError()), STATUS_TIMEOUT_MS);
    promise.then(
      (value) => {
        clearTimeout(timer);
        resolve(value);
      },
      (error: unknown) => {
        clearTimeout(timer);
        reject(error);
      },
    );
  });
}

function subscribeEvents(native: NativeBridge): void {
  if (subscribed) return;
  try {
    unsubscribe = native.on('app.stateChanged', (raw) => {
      const next = parseAppState(raw);
      if (!next) return;
      eventCount += 1;
      applyStatus(next);
    });
    subscribed = true;
  } catch {
    // Bridge revoked/unavailable: requests keep failing closed (no generation).
  }
}

/**
 * Read `app.status` once. A reply is applied only if no `app.stateChanged`
 * arrived while it was in flight (the pushed event is the newer truth).
 */
export function refreshAppStatus(): Promise<AppState | null> {
  const native = getBridge();
  if (!native) return Promise.resolve(null);
  subscribeEvents(native);
  if (seeding) return seeding;
  const seenEvents = eventCount;
  seeding = (async () => {
    try {
      const raw = await withTimeout(native.call('app.status', '{}'));
      const next = parseAppState(raw);
      if (next && eventCount === seenEvents) applyStatus(next);
    } catch {
      // Keep the previous status; callers fail closed if none is known.
    } finally {
      seeding = null;
    }
    return status;
  })();
  return seeding;
}

/** Seed (once) and subscribe; a no-op in browser mode. */
export function ensureNativeTarget(): void {
  if (isNativeTarget() && !status && !seeding) void refreshAppStatus();
  else if (isNativeTarget()) subscribeEvents(getBridge() as NativeBridge);
}

/**
 * Capture the generation for one request. Browser: `browser`. Desktop: the
 * native-issued value, waiting for the first `app.status` if needed; throws
 * TargetUnknownError when native has not reported one (nothing is sent).
 */
export async function captureTarget(): Promise<string> {
  const known = currentGeneration();
  if (known !== null) return known;
  await refreshAppStatus();
  const seeded = currentGeneration();
  if (seeded === null) throw new TargetUnknownError();
  return seeded;
}

/** True when a request captured under `captured` no longer matches. */
export function isStaleTarget(captured: string): boolean {
  return currentGeneration() !== captured;
}

/** The target header for a captured generation (none in browser mode). */
export function targetHeaders(captured: string): Record<string, string> {
  return isNativeTarget() ? { [TARGET_HEADER]: captured } : {};
}

/** Fires after a generation change, before status listeners (desktop only). */
export function onTargetGenerationChange(listener: (previous: string, next: string) => void): () => void {
  changeListeners.add(listener);
  return () => {
    changeListeners.delete(listener);
  };
}

export function subscribeAppStatus(listener: () => void): () => void {
  ensureNativeTarget();
  statusListeners.add(listener);
  return () => {
    statusListeners.delete(listener);
  };
}

// F2 editor custody. While an editor's query is held on an older target's
// data (lib/native/queryClient.ts), no change is sent to the current target.
let custodyHolds = 0;

/** Hold custody (one per suspended query); returns its idempotent release. */
export function holdTargetCustody(): () => void {
  custodyHolds += 1;
  let released = false;
  return () => {
    if (released) return;
    released = true;
    custodyHolds -= 1;
  };
}

/** True while any editor is held on an older target's data (desktop only). */
export function targetCustodyHeld(): boolean {
  return custodyHolds > 0;
}

/** Resolve once the current generation is known and differs from `from`. */
export function waitForTargetChange(from: string, signal: AbortSignal): Promise<string | null> {
  return new Promise((resolve) => {
    const done = (value: string | null): void => {
      off();
      signal.removeEventListener('abort', onAbort);
      resolve(value);
    };
    const onAbort = (): void => done(null);
    const off = onTargetGenerationChange((_previous, next) => {
      if (next !== from) done(next);
    });
    if (signal.aborted) return done(null);
    signal.addEventListener('abort', onAbort, { once: true });
    const now = currentGeneration();
    if (now !== null && now !== from) done(now);
  });
}

/**
 * Test-only (focused Node checks in web/scripts/*.test.mjs): install a fake
 * bridge (or none) and reset all module state. Production never calls it:
 * the bridge is detected from the app origin and `window.happyranchConnection`
 * only. ESLint/TypeScript cannot enforce test-only use; the `__…ForTests`
 * name follows the existing auth/notices/recovery reset convention.
 */
export function __setNativeBridgeForTests(next: NativeBridge | null): void {
  unsubscribe?.();
  custodyHolds = 0;
  bridge = next;
  status = null;
  keyGeneration = null;
  subscribed = false;
  unsubscribe = null;
  seeding = null;
  eventCount = 0;
  statusListeners.clear();
  changeListeners.clear();
}
