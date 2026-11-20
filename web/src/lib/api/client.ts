/**
 * Shared HTTP client for the daemon API.
 *
 * Every ``lib/api/*`` module composes this. Feature folders never call
 * ``fetch`` directly — see ``web/ARCHITECTURE.md``.
 */
import { clearToken, getToken } from '../auth';
import {
  clearNativeRecovery,
  parseNativeRecovery,
  reportNativeRecovery,
  type NativeRecoveryState,
} from '../nativeRecovery';
import { clearTransientNativeNotice, reportNativeNotice } from '../native/notices';
import {
  captureTarget,
  isNativeTarget,
  isStaleTarget,
  refreshAppStatus,
  targetHeaders,
} from '../native/target';

export const API_PREFIX = '/api/v1';

export interface RequestOptions {
  method?: 'GET' | 'POST' | 'PUT' | 'DELETE' | 'PATCH';
  body?: unknown;
  params?: Record<string, string | number | boolean | undefined | null>;
  signal?: AbortSignal;
  /** Additional request headers for endpoint-specific concurrency controls. */
  headers?: HeadersInit;
  /**
   * When set, overrides the default master daemon bearer with a scoped token.
   * Used by loopback-only, scoped-token-gated routes (e.g. register-binary).
   * Sends ``Authorization: Bearer <token>`` instead of the cached bearer.
   */
  auth?: { token: string };
  /**
   * Opt-in single send. `false`: on a master-bearer 401 the cached bearer is
   * still cleared and re-acquired, but the request is NOT re-sent; the 401 is
   * thrown so the caller's next explicit action uses the fresh bearer. Unset
   * keeps the default one re-send.
   */
  replayOnUnauthorized?: boolean;
}

export class ApiError extends Error {
  public override readonly name = 'ApiError';

  constructor(
    public readonly status: number,
    public readonly code: string | null,
    public readonly detail: unknown,
  ) {
    super(`API ${status}${code ? ` (${code})` : ''}`);
  }
}

/**
 * A native (desktop) recovery answer: exact `{"detail":{"code":C}}` with 503
 * for no_target/target_starting/target_unreachable/incompatible/busy or 409
 * for target_changed. It is still an `ApiError` (same status/code/detail) so
 * existing per-feature error handling is unchanged; it is never an auth
 * failure and never clears the bearer. See `lib/nativeRecovery.ts`.
 */
export class NativeRecoveryError extends ApiError {
  constructor(public readonly recovery: NativeRecoveryState, detail: unknown) {
    super(recovery.code === 'target_changed' ? 409 : 503, recovery.code, detail);
  }
}

/**
 * If `status`/`body` is exactly a native recovery answer, record it in the
 * app-wide recovery store and return the typed error; otherwise null.
 */
export function nativeRecoveryErrorFor(status: number, body: unknown): NativeRecoveryError | null {
  const recovery = parseNativeRecovery(status, body);
  if (!recovery) return null;
  if (recovery.code === 'busy') {
    // W-1: host busy is transient and never page-blocking.
    reportNativeNotice('busy');
  } else if (!(recovery.code === 'target_changed' && isNativeTarget())) {
    // Desktop 409 is handled by re-reading app.status (schemeFetch), not by
    // a page state; browser mode keeps the S1b behavior.
    reportNativeRecovery(recovery);
  }
  return new NativeRecoveryError(recovery, (body as { detail: unknown }).detail);
}

// ---------------------------------------------------------------------------
// Native bridge v2 request fence (THR-387 S5-W1; spec §12)
// ---------------------------------------------------------------------------

/**
 * A response (or failure) for a request captured under an older target
 * generation. It is dropped: no cache write, toast or draft update. It is a
 * `NativeRecoveryError` (409 target_changed) so latched read owners retry it
 * like an explicit Retry; it is never recorded as a page recovery state.
 */
export class StaleTargetError extends NativeRecoveryError {
  constructor() {
    super({ code: 'target_changed' }, { code: 'target_changed' });
  }
}

/**
 * A change refused by native before it was forwarded (409 target_changed, or
 * no generation known yet). It was not sent and is never retried
 * automatically; the caller's draft is untouched.
 */
export class NotSentError extends NativeRecoveryError {
  constructor() {
    super({ code: 'target_changed' }, { code: 'target_changed' });
  }
}

/**
 * A forwarded change interrupted by a target switch (499 cancelled, a stream
 * error, or a response that arrived after the switch). Its outcome is
 * unknown; it is never retried automatically.
 */
export class ResultUnknownError extends ApiError {
  constructor() {
    super(499, 'cancelled', { code: 'cancelled' });
  }
}

/** Native per-view cap / lock contention (429 too_many_requests); not host busy. */
export class ThrottledError extends ApiError {
  constructor() {
    super(429, 'too_many_requests', { code: 'too_many_requests' });
  }
}

/** Backoff for transient native answers on reads (GET only). */
export const TRANSIENT_RETRY_DELAYS_MS: readonly number[] = [500, 1000, 2000];

/** Exactly `{"detail":{"code":C}}` (incompatible may add side); else null. */
function nativeCodeOf(body: unknown): string | null {
  if (!body || typeof body !== 'object' || Array.isArray(body)) return null;
  const keys = Object.keys(body);
  if (keys.length !== 1 || keys[0] !== 'detail') return null;
  const detail = (body as { detail: unknown }).detail;
  if (!detail || typeof detail !== 'object' || Array.isArray(detail)) return null;
  const code = (detail as { code?: unknown }).code;
  if (typeof code !== 'string') return null;
  const extra = Object.keys(detail).filter((k) => k !== 'code');
  if (extra.length === 0 || (code === 'incompatible' && extra.length === 1 && extra[0] === 'side')) return code;
  return null;
}

async function peekNativeCode(res: Response): Promise<string | null> {
  if (res.status !== 409 && res.status !== 429 && res.status !== 499 && res.status !== 503) return null;
  try {
    return nativeCodeOf(JSON.parse(await res.clone().text()));
  } catch {
    return null;
  }
}

function sleep(ms: number, signal?: AbortSignal | null): Promise<void> {
  return new Promise((resolve, reject) => {
    if (signal?.aborted) return reject(new DOMException('Aborted', 'AbortError'));
    const timer = setTimeout(() => {
      signal?.removeEventListener('abort', onAbort);
      resolve();
    }, ms);
    const onAbort = (): void => {
      clearTimeout(timer);
      reject(new DOMException('Aborted', 'AbortError'));
    };
    signal?.addEventListener('abort', onAbort, { once: true });
  });
}

/** Outcome for a request whose captured generation is no longer current. */
export function staleOutcome(read: boolean): Error {
  if (read) return new StaleTargetError();
  reportNativeNotice('result_unknown');
  return new ResultUnknownError();
}

function notSent(): NotSentError {
  reportNativeNotice('not_sent');
  return new NotSentError();
}

/** A successful forwarded response clears recovery and busy/throttled (W-2a). */
export function noteForwardedSuccess(captured: string): void {
  if (isStaleTarget(captured)) return;
  clearNativeRecovery();
  clearTransientNativeNotice();
}

export interface Fenced {
  res: Response;
  /** Generation captured at request start (`browser` without a bridge). */
  captured: string;
}

/**
 * Send one scheme API request under the captured target generation.
 *
 * - Captures the generation at start and sends `X-HappyRanch-Target` (desktop
 *   only; never for GET /auth/bootstrap, which does not use this helper).
 * - Stale (generation changed): GET -> StaleTargetError; others ->
 *   ResultUnknownError. Nothing is retried.
 * - 429 too_many_requests / 503 busy: non-blocking notice; GET retries with
 *   backoff, others fail at once (not sent).
 * - Desktop 409 target_changed: re-read app.status. GET refetches once if the
 *   generation is unchanged (a changed generation is refetched by its new
 *   owners); others -> NotSentError, never retried.
 * - 499 cancelled on a non-GET -> ResultUnknownError.
 * Everything else is returned for the caller's existing error handling.
 */
export async function schemeFetch(url: string, init: RequestInit): Promise<Fenced> {
  const read = (init.method ?? 'GET').toUpperCase() === 'GET';
  let captured: string;
  try {
    captured = await captureTarget();
  } catch {
    if (!read) throw notSent();
    reportNativeRecovery({ code: 'target_changed' });
    throw new NativeRecoveryError({ code: 'target_changed' }, { code: 'target_changed' });
  }
  const headers = new Headers(init.headers);
  for (const [k, v] of Object.entries(targetHeaders(captured))) headers.set(k, v);
  let refetched = false;
  for (let attempt = 0; ; attempt += 1) {
    let res: Response;
    try {
      res = await fetch(url, { ...init, headers });
    } catch (error) {
      if (isStaleTarget(captured)) throw staleOutcome(read);
      throw error;
    }
    if (isStaleTarget(captured)) throw staleOutcome(read);
    if (res.ok) {
      noteForwardedSuccess(captured);
      return { res, captured };
    }
    const code = await peekNativeCode(res);
    const transient = (res.status === 429 && code === 'too_many_requests') || (res.status === 503 && code === 'busy');
    if (transient) {
      reportNativeNotice(res.status === 429 ? 'throttled' : 'busy');
      if (read && attempt < TRANSIENT_RETRY_DELAYS_MS.length) {
        try {
          await sleep(TRANSIENT_RETRY_DELAYS_MS[attempt] as number, init.signal);
        } catch (error) {
          if (isStaleTarget(captured)) throw staleOutcome(read);
          throw error;
        }
        if (isStaleTarget(captured)) throw staleOutcome(read);
        continue;
      }
      if (res.status === 429) throw new ThrottledError();
      throw new NativeRecoveryError({ code: 'busy' }, { code: 'busy' });
    }
    if (res.status === 409 && code === 'target_changed' && isNativeTarget()) {
      await refreshAppStatus();
      if (!read) throw notSent();
      if (isStaleTarget(captured)) throw new StaleTargetError();
      if (!refetched) {
        refetched = true;
        continue;
      }
      reportNativeRecovery({ code: 'target_changed' });
      throw new NativeRecoveryError({ code: 'target_changed' }, { code: 'target_changed' });
    }
    if (res.status === 499 && code === 'cancelled') throw staleOutcome(read);
    return { res, captured };
  }
}

/**
 * Read a fenced response body. A body read that fails, or that completes
 * after a generation change, is stale (GET) or of unknown outcome (others).
 */
export async function fencedText(fenced: Fenced, read: boolean): Promise<string> {
  let text: string;
  try {
    text = await fenced.res.text();
  } catch (error) {
    if (isStaleTarget(fenced.captured)) throw staleOutcome(read);
    if (!read && isNativeTarget()) throw staleOutcome(read);
    throw error;
  }
  if (isStaleTarget(fenced.captured)) throw staleOutcome(read);
  return text;
}

function buildUrl(path: string, params?: RequestOptions['params']): string {
  const base = `${API_PREFIX}${path.startsWith('/') ? path : `/${path}`}`;
  if (!params) return base;
  const qs = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) {
    if (v === undefined || v === null) continue;
    qs.set(k, String(v));
  }
  const tail = qs.toString();
  return tail ? `${base}?${tail}` : base;
}

function parseError(status: number, body: unknown): ApiError {
  // FastAPI's structured errors look like {"detail": {"code": "...", ...}}.
  // Some endpoints use a plain string detail. Both shapes flow through here.
  let code: string | null = null;
  let detail: unknown = body;
  if (
    body &&
    typeof body === 'object' &&
    'detail' in body &&
    (body as { detail: unknown }).detail !== undefined
  ) {
    detail = (body as { detail: unknown }).detail;
    if (
      detail &&
      typeof detail === 'object' &&
      'code' in detail &&
      typeof (detail as { code?: unknown }).code === 'string'
    ) {
      code = (detail as { code: string }).code;
    }
  }
  return new ApiError(status, code, detail);
}

async function doFetch(
  url: string,
  init: RequestInit,
  token: string,
): Promise<Fenced> {
  const headers = new Headers(init.headers);
  headers.set('Authorization', `Bearer ${token}`);
  if (init.body !== undefined && !headers.has('Content-Type')) {
    headers.set('Content-Type', 'application/json');
  }
  headers.set('Accept', 'application/json');
  return schemeFetch(url, { ...init, headers, credentials: 'same-origin' });
}

export async function request<T = unknown>(
  path: string,
  opts: RequestOptions = {},
): Promise<T> {
  const url = buildUrl(path, opts.params);
  const init: RequestInit = {
    method: opts.method ?? 'GET',
    signal: opts.signal,
    headers: opts.headers,
  };
  if (opts.body !== undefined) {
    init.body = JSON.stringify(opts.body);
  }

  let token: string;
  if (opts.auth) {
    // Scoped-token path: use the caller-supplied token.
    token = opts.auth.token;
  } else {
    token = await getToken();
  }
  let fenced = await doFetch(url, init, token);

  if (fenced.res.status === 401 && !opts.auth) {
    // Master-bearer retry (scoped tokens are single-use, so skip).
    clearToken();
    token = await getToken();
    if (opts.replayOnUnauthorized !== false) {
      fenced = await doFetch(url, init, token);
    }
  }
  const res = fenced.res;
  const read = (init.method ?? 'GET') === 'GET';

  if (res.status === 204) {
    return undefined as T;
  }

  let body: unknown = null;
  const text = await fencedText(fenced, read);
  if (text) {
    try {
      body = JSON.parse(text);
    } catch {
      body = text;
    }
  }

  if (!res.ok) {
    throw nativeRecoveryErrorFor(res.status, body) ?? parseError(res.status, body);
  }
  return body as T;
}
