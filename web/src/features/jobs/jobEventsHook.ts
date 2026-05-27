import { useEffect, useState } from 'react';
import {
  EventStreamContentType,
  fetchEventSource,
} from '@microsoft/fetch-event-source';
import { getToken } from '@/lib/auth';
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
): { events: JobEvent[]; terminal: JobEvent | null } {
  const [events, setEvents] = useState<JobEvent[]>([]);
  const [terminal, setTerminal] = useState<JobEvent | null>(null);

  useEffect(() => {
    // Reset on every dep change so navigating between jobs (the drawer is
    // shared across all job ids) doesn't show stale lines or a stale
    // terminal banner from the previously-streamed job.
    setEvents([]);
    setTerminal(null);
    if (!path || !active) return;
    const controller = new AbortController();
    let cancelled = false;

    void (async () => {
      const token = await getToken();
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
            controller.abort();
          }
        },
        onerror(err) {
          throw err; // stop retry loop
        },
      }).catch(() => {
        // swallow abort / normal close
      });
    })();

    return () => {
      cancelled = true;
      controller.abort();
    };
  }, [path, active]);

  return { events, terminal };
}
