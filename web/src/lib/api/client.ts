/**
 * Shared HTTP client for the daemon API.
 *
 * Every ``lib/api/*`` module composes this. Feature folders never call
 * ``fetch`` directly — see ``web/ARCHITECTURE.md``.
 */
import { clearToken, getToken } from '../auth';

export const API_PREFIX = '/api/v1';

export interface RequestOptions {
  method?: 'GET' | 'POST' | 'PUT' | 'DELETE' | 'PATCH';
  body?: unknown;
  params?: Record<string, string | number | boolean | undefined | null>;
  signal?: AbortSignal;
  /**
   * When set, overrides the default master daemon bearer with a scoped token.
   * Used by loopback-only, scoped-token-gated routes (e.g. register-binary).
   * Sends ``Authorization: Bearer <token>`` instead of the cached bearer.
   */
  auth?: { token: string };
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
): Promise<Response> {
  const headers = new Headers(init.headers);
  headers.set('Authorization', `Bearer ${token}`);
  if (init.body !== undefined && !headers.has('Content-Type')) {
    headers.set('Content-Type', 'application/json');
  }
  headers.set('Accept', 'application/json');
  return fetch(url, { ...init, headers, credentials: 'same-origin' });
}

export async function request<T = unknown>(
  path: string,
  opts: RequestOptions = {},
): Promise<T> {
  const url = buildUrl(path, opts.params);
  const init: RequestInit = {
    method: opts.method ?? 'GET',
    signal: opts.signal,
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
  let res = await doFetch(url, init, token);

  if (res.status === 401 && !opts.auth) {
    // Master-bearer retry (scoped tokens are single-use, so skip).
    clearToken();
    token = await getToken();
    res = await doFetch(url, init, token);
  }

  if (res.status === 204) {
    return undefined as T;
  }

  let body: unknown = null;
  const text = await res.text();
  if (text) {
    try {
      body = JSON.parse(text);
    } catch {
      body = text;
    }
  }

  if (!res.ok) {
    throw parseError(res.status, body);
  }
  return body as T;
}
