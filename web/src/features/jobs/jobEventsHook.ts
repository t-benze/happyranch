import { useEffect, useState } from 'react';
import {
  EventStreamContentType,
  fetchEventSource,
} from '@microsoft/fetch-event-source';
import { getToken } from '@/lib/auth';
// eslint-disable-next-line no-restricted-imports -- no @/hooks accessor exposes API_PREFIX; routed direct per THR-011 founder ruling (option 3), pending a future hook
import { API_PREFIX } from '@/lib/api/client';

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

  useEffect(() => {
    setEvents([]);
    setTerminal(null);
    setUnavailable(false);
  }, [path]);

  useEffect(() => {
    if (!path || !active) return;
    const controller = new AbortController();
    let cancelled = false;
    let intentionalEnd = false;

    void (async () => {
      try {
        const token = await getToken();
        if (cancelled) return;
        await fetchEventSource(`${API_PREFIX}${path}`, {
          signal: controller.signal,
          headers: {
            Authorization: `Bearer ${token}`,
            Accept: 'text/event-stream',
          },
          openWhenHidden: true,
          async onopen(resp) {
            if (
              resp.ok &&
              resp.headers.get('content-type')?.includes(EventStreamContentType)
            ) {
              return;
            }
            throw new Error(`SSE failed: ${resp.status}`);
          },
          onmessage(ev) {
            if (cancelled) return;
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
            throw err; // stop retry loop
          },
        });
      } catch {
        if (!cancelled && !intentionalEnd) setUnavailable(true);
      }
    })();

    return () => {
      cancelled = true;
      intentionalEnd = true;
      controller.abort();
    };
  }, [path, active]);

  return { events, terminal, unavailable };
}
