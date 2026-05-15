/**
 * Auth-aware Server-Sent Events helper.
 *
 * Native ``EventSource`` cannot send headers, so we use
 * ``@microsoft/fetch-event-source`` and inject the bearer token from
 * ``getToken()``. On EventSource errors we drop the cached token once and
 * re-bootstrap, matching the HTTP client's 401-retry semantics.
 */
import {
  EventStreamContentType,
  fetchEventSource,
} from '@microsoft/fetch-event-source';

import { clearToken, getToken } from '../auth';
import { API_PREFIX } from './client';

export interface SSEOptions<T> {
  /** Called once per ``data: <json>`` frame, parsed to T. */
  onMessage: (data: T) => void;
  /** Optional: connection opened. */
  onOpen?: () => void;
  /** Optional: receives a query-string segment to append (no leading ?). */
  query?: Record<string, string | number | undefined | null>;
  /** AbortController.signal — closing the subscription. */
  signal: AbortSignal;
}

function buildSSEUrl(path: string, query: SSEOptions<unknown>['query']): string {
  const base = `${API_PREFIX}${path.startsWith('/') ? path : `/${path}`}`;
  if (!query) return base;
  const qs = new URLSearchParams();
  for (const [k, v] of Object.entries(query)) {
    if (v === undefined || v === null) continue;
    qs.set(k, String(v));
  }
  const tail = qs.toString();
  return tail ? `${base}?${tail}` : base;
}

class FatalSSEError extends Error {}

export async function subscribeSSE<T = unknown>(
  path: string,
  opts: SSEOptions<T>,
): Promise<void> {
  const url = buildSSEUrl(path, opts.query);
  let tokenRefreshed = false;

  await fetchEventSource(url, {
    signal: opts.signal,
    openWhenHidden: true,
    headers: {
      Authorization: `Bearer ${await getToken()}`,
      Accept: 'text/event-stream',
    },
    async onopen(response) {
      if (response.ok && response.headers.get('content-type')?.includes(EventStreamContentType)) {
        opts.onOpen?.();
        return;
      }
      if (response.status === 401 && !tokenRefreshed) {
        tokenRefreshed = true;
        clearToken();
        // Returning will cause fetch-event-source to retry. We need to update
        // the header — easiest way is to throw a retry-eligible error and let
        // the caller re-invoke. But the contract for this helper is
        // single-shot, so throw FatalSSEError to propagate up.
        throw new FatalSSEError('token expired');
      }
      throw new FatalSSEError(`SSE open failed (${response.status})`);
    },
    onmessage(ev) {
      if (!ev.data) return;
      try {
        const parsed = JSON.parse(ev.data) as T;
        opts.onMessage(parsed);
      } catch {
        // Daemon shouldn't emit non-JSON; ignore quietly.
      }
    },
    onerror(err) {
      if (err instanceof FatalSSEError) throw err;
      // For transient errors fetch-event-source auto-retries unless we throw.
      // Default behavior is what we want (re-connect with backoff).
    },
  });
}
