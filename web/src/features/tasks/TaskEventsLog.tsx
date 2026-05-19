import { useCallback, useRef, useState } from 'react';
import { useTaskTailSSE } from '@/hooks/tasks';
import type { TaskEvent } from '@/lib/api/types';

/**
 * Stable signature for an SSE TaskEvent. EventBus.subscribe replays the full
 * task history on every reconnect, and fetch-event-source reconnects on
 * transient failures — so the same event can arrive more than once. Dedup
 * client-side by hashing the full payload, since the daemon doesn't expose a
 * sequence/cursor (unlike threads' `since_seq`).
 */
function signature(ev: TaskEvent): string {
  return JSON.stringify([ev.timestamp, ev.type, ev.agent ?? null, ev.payload ?? null]);
}

export function TaskEventsLog({ taskId }: { taskId: string }): JSX.Element {
  const [events, setEvents] = useState<TaskEvent[]>([]);
  const seen = useRef<Set<string>>(new Set());

  const append = useCallback((ev: TaskEvent) => {
    const sig = signature(ev);
    if (seen.current.has(sig)) return;
    seen.current.add(sig);
    setEvents((prev) => [...prev, ev]);
  }, []);

  useTaskTailSSE(taskId, append);

  if (events.length === 0) {
    return <p className="text-fg-muted text-xs">Waiting for events…</p>;
  }
  return (
    <ol className="space-y-1 text-xs">
      {events.map((ev) => (
        <li key={signature(ev)} className="flex gap-2">
          <span className="text-fg-muted font-mono">{ev.timestamp}</span>
          <span className="text-fg font-medium">{ev.type}</span>
          {ev.agent && <span className="text-fg-muted">· {ev.agent}</span>}
        </li>
      ))}
    </ol>
  );
}
