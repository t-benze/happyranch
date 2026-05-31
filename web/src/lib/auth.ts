/**
 * Token bootstrap for the SPA.
 *
 * On cold start the SPA calls ``GET /api/v1/auth/bootstrap`` once (localhost-
 * gated on the daemon side), then stashes the returned bearer in
 * ``sessionStorage``. Subsequent requests read from cache.
 */

const STORAGE_KEY = 'happyranch.token';
const BOOTSTRAP_URL = '/api/v1/auth/bootstrap';

let inflight: Promise<string> | null = null;

export class BootstrapError extends Error {
  constructor(public readonly status: number, public readonly detail: unknown) {
    super(`bootstrap failed (HTTP ${status})`);
    this.name = 'BootstrapError';
  }
}

export async function getToken(): Promise<string> {
  const cached = sessionStorage.getItem(STORAGE_KEY);
  if (cached) return cached;
  if (inflight) return inflight;
  inflight = (async () => {
    try {
      const r = await fetch(BOOTSTRAP_URL, { credentials: 'same-origin' });
      if (!r.ok) {
        let detail: unknown = null;
        try {
          detail = await r.json();
        } catch {
          detail = await r.text().catch(() => null);
        }
        throw new BootstrapError(r.status, detail);
      }
      const body = (await r.json()) as { token: string };
      sessionStorage.setItem(STORAGE_KEY, body.token);
      return body.token;
    } finally {
      inflight = null;
    }
  })();
  return inflight;
}

export function clearToken(): void {
  sessionStorage.removeItem(STORAGE_KEY);
}

/** Test-only: drop the in-memory inflight ref + the storage entry. */
export function __resetTokenCacheForTests(): void {
  inflight = null;
  sessionStorage.removeItem(STORAGE_KEY);
}
