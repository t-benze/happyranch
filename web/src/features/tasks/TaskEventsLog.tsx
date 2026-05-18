import { useCallback, useState } from 'react';
import { useTaskTailSSE } from '@/hooks/tasks';
import type { TaskEvent } from '@/lib/api/types';

export function TaskEventsLog({ taskId }: { taskId: string }): JSX.Element {
  const [events, setEvents] = useState<TaskEvent[]>([]);
  const append = useCallback((ev: TaskEvent) => setEvents((prev) => [...prev, ev]), []);
  useTaskTailSSE(taskId, append);

  if (events.length === 0) {
    return <p className="text-fg-muted text-xs">Waiting for events…</p>;
  }
  return (
    <ol className="space-y-1 text-xs">
      {events.map((ev, i) => (
        <li key={i} className="flex gap-2">
          <span className="text-fg-muted font-mono">{ev.timestamp}</span>
          <span className="text-fg font-medium">{ev.type}</span>
          {ev.agent && <span className="text-fg-muted">· {ev.agent}</span>}
        </li>
      ))}
    </ol>
  );
}
