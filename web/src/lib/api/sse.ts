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
import { reportNativeNotice } from '../native/notices';
import {
  captureTarget,
  isNativeTarget,
  isStaleTarget,
  onTargetGenerationChange,
  refreshAppStatus,
  targetHeaders,
  waitForTargetChange,
} from '../native/target';
import { reportNativeRecovery } from '../nativeRecovery';
import {
  API_PREFIX,
  NativeRecoveryError,
  TRANSIENT_RETRY_DELAYS_MS,
  nativeRecoveryErrorFor,
  noteForwardedSuccess,
} from './client';

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

/** 503 busy / 429 too_many_requests at open: retry with capped backoff. */
class TransientOpenError extends Error {}

/** Desktop 409 target_changed at open: resubscribe after the next generation. */
class TargetChangedOpenError extends Error {}

const MAX_TRANSIENT_DELAY_MS = 5000;

function transientDelay(attempt: number): number {
  return TRANSIENT_RETRY_DELAYS_MS[attempt] ?? MAX_TRANSIENT_DELAY_MS;
}

/**
 * Subscribe to a daemon SSE stream.
 *
 * Desktop (native bridge v2): each connection captures the target generation
 * and sends `X-HappyRanch-Target`. A generation change aborts the old stream;
 * it resubscribes only once the new generation is known, while the caller's
 * signal is still open. A 409 target_changed at open re-reads app.status and
 * waits for the new generation. Browser mode: one connection, as before.
 */
export async function subscribeSSE<T = unknown>(
  path: string,
  opts: SSEOptions<T>,
): Promise<void> {
  const url = buildSSEUrl(path, opts.query);
  while (!opts.signal.aborted) {
    let captured: string;
    try {
      captured = await captureTarget();
    } catch {
      reportNativeRecovery({ code: 'target_changed' });
      throw new NativeRecoveryError({ code: 'target_changed' }, { code: 'target_changed' });
    }
    const outcome = await connectOnce(url, opts, captured);
    if (outcome === 'done' || opts.signal.aborted) return;
    // 'changed': resubscribe only once the new generation is known.
    if (outcome === 'target_changed') {
      await refreshAppStatus();
      if (!isStaleTarget(captured)) await waitForTargetChange(captured, opts.signal);
    }
  }
}

async function connectOnce<T>(
  url: string,
  opts: SSEOptions<T>,
  captured: string,
): Promise<'done' | 'changed' | 'target_changed'> {
  const controller = new AbortController();
  const onAbort = (): void => controller.abort();
  opts.signal.addEventListener('abort', onAbort, { once: true });
  let changed = false;
  const offChange = onTargetGenerationChange(() => {
    // Abort the old-generation stream; the loop resubscribes.
    changed = true;
    controller.abort();
  });
  let tokenRefreshed = false;
  let transientAttempts = 0;
  try {
    await fetchEventSource(url, {
      signal: controller.signal,
      openWhenHidden: true,
      headers: {
        Authorization: `Bearer ${await getToken()}`,
        Accept: 'text/event-stream',
        ...targetHeaders(captured),
      },
      async onopen(response) {
        if (isStaleTarget(captured)) {
          changed = true;
          controller.abort();
          return;
        }
        if (response.ok && response.headers.get('content-type')?.includes(EventStreamContentType)) {
          transientAttempts = 0;
          noteForwardedSuccess(captured);
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
        if (response.status === 503 || response.status === 409 || response.status === 429) {
          const text = await response.text().catch(() => '');
          let body: unknown = null;
          try {
            body = JSON.parse(text);
          } catch {
            body = null;
          }
          if (response.status === 429) {
            const detail = (body as { detail?: unknown } | null)?.detail;
            if (
              body && Object.keys(body).length === 1 && detail && typeof detail === 'object' &&
              Object.keys(detail).length === 1 && (detail as { code?: unknown }).code === 'too_many_requests'
            ) {
              reportNativeNotice('throttled');
              throw new TransientOpenError('too_many_requests');
            }
          } else {
            // Native recovery answer: surface the state and stop retrying
            // (busy: transient notice and backoff, W-1). Any other 503/409
            // keeps the existing single-shot failure below.
            const recovery = nativeRecoveryErrorFor(response.status, body);
            if (recovery?.code === 'busy') throw new TransientOpenError('busy');
            if (recovery?.code === 'target_changed' && isNativeTarget()) throw new TargetChangedOpenError();
            if (recovery) throw recovery;
          }
        }
        throw new FatalSSEError(`SSE open failed (${response.status})`);
      },
      onmessage(ev) {
        if (!ev.data || isStaleTarget(captured)) return;
        try {
          const parsed = JSON.parse(ev.data) as T;
          opts.onMessage(parsed);
        } catch {
          // Daemon shouldn't emit non-JSON; ignore quietly.
        }
      },
      onerror(err) {
        if (err instanceof TransientOpenError) {
          const delay = transientDelay(transientAttempts);
          transientAttempts += 1;
          return delay;
        }
        if (
          err instanceof FatalSSEError ||
          err instanceof NativeRecoveryError ||
          err instanceof TargetChangedOpenError
        ) {
          throw err;
        }
        // For transient errors fetch-event-source auto-retries unless we throw.
        // Default behavior is what we want (re-connect with backoff).
        return undefined;
      },
    });
  } catch (error) {
    if (error instanceof TargetChangedOpenError) return 'target_changed';
    if (changed || isStaleTarget(captured)) return 'changed';
    throw error;
  } finally {
    offChange();
    opts.signal.removeEventListener('abort', onAbort);
  }
  return changed ? 'changed' : 'done';
}
