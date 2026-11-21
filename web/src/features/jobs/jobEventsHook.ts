import { useEffect, useState } from 'react';
import {
  EventStreamContentType,
  fetchEventSource,
} from '@microsoft/fetch-event-source';
import { getToken } from '@/lib/auth';
// eslint-disable-next-line no-restricted-imports -- no @/hooks accessor exposes API_PREFIX; routed direct per THR-011 founder ruling (option 3), pending a future hook
import { API_PREFIX, TRANSIENT_RETRY_DELAYS_MS, nativeRecoveryErrorFor, noteForwardedSuccess } from '@/lib/api/client';
import { useTargetGeneration } from '@/lib/native/hooks';
import { reportNativeNotice } from '@/lib/native/notices';
import { isNativeTarget, isStaleTarget, refreshAppStatus, targetHeaders } from '@/lib/native/target';

/** 503 busy / 429 too_many_requests at open: retry with capped backoff (W-1). */
class TransientOpenError extends Error {}

export interface JobEvent {
  kind: 'stdout' | 'stderr' | 'terminal';
  line?: string;
  status?: string;
  exit_code?: number | null;
  duration_ms?: number | null;
  reason?: string | null;
}

export function useJobEventStream(
  path: string | null,
  active: boolean,
): { events: JobEvent[]; terminal: JobEvent | null; unavailable: boolean } {
  const [events, setEvents] = useState<JobEvent[]>([]);
  const [terminal, setTerminal] = useState<JobEvent | null>(null);

  const [unavailable, setUnavailable] = useState(false);
  // Desktop: the stream belongs to one target generation. A change aborts it
  // (effect cleanup) and resubscribes once the new generation is known; the
  // event log restarts for the new target. Browser: constant `browser`.
  const generation = useTargetGeneration();

  useEffect(() => {
    setEvents([]);
    setTerminal(null);
    setUnavailable(false);
  }, [path, generation]);

  useEffect(() => {
    if (!path || !active || generation === null) return;
    const captured = generation;
    const controller = new AbortController();
    let cancelled = false;
    let intentionalEnd = false;
    let transientAttempts = 0;

    void (async () => {
      try {
        const token = await getToken();
        if (cancelled) return;
        await fetchEventSource(`${API_PREFIX}${path}`, {
          signal: controller.signal,
          headers: {
            Authorization: `Bearer ${token}`,
            Accept: 'text/event-stream',
            ...targetHeaders(captured),
          },
          openWhenHidden: true,
          async onopen(resp) {
            if (isStaleTarget(captured)) {
              intentionalEnd = true;
              controller.abort();
              return;
            }
            if (
              resp.ok &&
              resp.headers.get('content-type')?.includes(EventStreamContentType)
            ) {
              transientAttempts = 0;
              noteForwardedSuccess(captured);
              return;
            }
            if (resp.status === 503 || resp.status === 409 || resp.status === 429) {
              // Native recovery answer: record the state; the throw below
              // already stops the retry loop exactly as for other failures.
              // Busy / too_many_requests are transient (backoff retry).
              const body: unknown = await resp.json().catch(() => null);
              // F4: an old body never publishes for the new target.
              if (isStaleTarget(captured)) {
                intentionalEnd = true;
                controller.abort();
                return;
              }
              if (resp.status === 429) {
                const detail = (body as { detail?: unknown } | null)?.detail;
                if (
                  body && Object.keys(body).length === 1 && detail && typeof detail === 'object' &&
                  Object.keys(detail).length === 1 && (detail as { code?: unknown }).code === 'too_many_requests'
                ) {
                  reportNativeNotice('throttled');
                  throw new TransientOpenError('too_many_requests');
                }
              } else {
                const recovery = nativeRecoveryErrorFor(resp.status, body);
                if (recovery?.code === 'busy') throw new TransientOpenError('busy');
                if (recovery?.code === 'target_changed' && isNativeTarget()) {
                  // Re-read app.status; a new generation re-runs this effect.
                  void refreshAppStatus();
                }
                if (recovery) throw recovery;
              }
            }
            throw new Error(`SSE failed: ${resp.status}`);
          },
          onmessage(ev) {
            if (cancelled || isStaleTarget(captured)) return;
            const data: Record<string, unknown> = ev.data
              ? (JSON.parse(ev.data) as Record<string, unknown>)
              : {};
            if (ev.event === 'stdout' || ev.event === 'stderr') {
              setEvents((prev) => [
                ...prev,
                { kind: ev.event as 'stdout' | 'stderr', line: (data.line as string) ?? '' },
              ]);
            } else if (ev.event === 'terminal') {
              setTerminal({
                kind: 'terminal',
                status: data.status as string | undefined,
                exit_code: data.exit_code as number | null | undefined,
                duration_ms: data.duration_ms as number | null | undefined,
                reason: data.reason as string | null | undefined,
              });
              intentionalEnd = true;
              controller.abort();
            }
          },
          onclose() {
            if (!intentionalEnd && !cancelled) throw new Error('Job stream closed');
          },
          onerror(err) {
            if (err instanceof TransientOpenError) {
              const delay = TRANSIENT_RETRY_DELAYS_MS[transientAttempts] ?? 5000;
              transientAttempts += 1;
              return delay;
            }
            throw err; // stop retry loop
          },
        });
      } catch {
        if (!cancelled && !intentionalEnd && !isStaleTarget(captured)) setUnavailable(true);
      }
    })();

    return () => {
      cancelled = true;
      intentionalEnd = true;
      controller.abort();
    };
  }, [path, active, generation]);

  return { events, terminal, unavailable };
}
