/**
 * Token bootstrap for the SPA.
 *
 * On cold start the SPA calls ``GET /api/v1/auth/bootstrap`` once (localhost-
 * gated on the daemon side), then stashes the returned bearer in
 * ``sessionStorage``. Subsequent requests read from cache.
 */

import { reportNativeNotice } from './native/notices';
import { currentGeneration, isNativeTarget } from './native/target';
import { parseNativeRecovery, reportNativeRecovery } from './nativeRecovery';

const STORAGE_KEY = 'happyranch.token';
const BOOTSTRAP_URL = '/api/v1/auth/bootstrap';

// One shared in-flight bootstrap per native target generation (R3-1): callers
// under the current generation never inherit an older generation's pending
// bootstrap (its stale rejection would strand them). Browser: one slot.
let inflight: Promise<string> | null = null;
let inflightGeneration: string | null = null;
let inflightSlot: object | null = null;

export class BootstrapError extends Error {
  constructor(public readonly status: number, public readonly detail: unknown) {
    super(`bootstrap failed (HTTP ${status})`);
    this.name = 'BootstrapError';
  }
}

/**
 * Desktop: the native target generation changed while a failed bootstrap's
 * error body was being read (R2-3). The body belongs to the older target, so
 * nothing was published; callers classify it as stale (read) or not sent
 * (change) via `pinnedToken` in lib/api/client.ts.
 */
export class BootstrapStaleError extends Error {
  public override readonly name = 'BootstrapStaleError';
  constructor() {
    super('bootstrap answer belongs to an older native target');
  }
}

export async function getToken(): Promise<string> {
  const cached = sessionStorage.getItem(STORAGE_KEY);
  if (cached) return cached;
  const native = isNativeTarget();
  const captured = native ? currentGeneration() : null;
  if (inflight && inflightGeneration === captured) return inflight;
  // The bootstrap request carries no target header, but its error side
  // effects are fenced like every other answer: one generation for the
  // shared in-flight bootstrap, captured at its start (desktop only). An
  // older generation's bootstrap keeps running for its own callers only.
  const slot = {};
  inflightSlot = slot;
  inflightGeneration = captured;
  const pending: Promise<string> = (async () => {
    try {
      const r = await fetch(BOOTSTRAP_URL, { credentials: 'same-origin' });
      if (!r.ok) {
        let detail: unknown = null;
        try {
          detail = await r.json();
        } catch {
          detail = await r.text().catch(() => null);
        }
        // F4/R2-3: an old target's error body never publishes a notice or
        // recovery state for the current target.
        if (native && currentGeneration() !== captured) throw new BootstrapStaleError();
        // A native recovery answer (desktop: no target selected, target
        // starting, ...) is recorded as a recovery state, not an auth failure.
        // Host busy is transient and never page-blocking (THR-387 W-1).
        const recovery = parseNativeRecovery(r.status, detail);
        if (recovery?.code === 'busy') reportNativeNotice('busy');
        else if (recovery) reportNativeRecovery(recovery);
        throw new BootstrapError(r.status, detail);
      }
      const body = (await r.json()) as { token: string };
      sessionStorage.setItem(STORAGE_KEY, body.token);
      return body.token;
    } finally {
      if (inflightSlot === slot) {
        inflight = null;
        inflightGeneration = null;
        inflightSlot = null;
      }
    }
  })();
  if (inflightSlot === slot) inflight = pending;
  return pending;
}

export function clearToken(): void {
  sessionStorage.removeItem(STORAGE_KEY);
}

/** Test-only: drop the in-memory inflight ref + the storage entry. */
export function __resetTokenCacheForTests(): void {
  inflight = null;
  inflightGeneration = null;
  inflightSlot = null;
  sessionStorage.removeItem(STORAGE_KEY);
}
