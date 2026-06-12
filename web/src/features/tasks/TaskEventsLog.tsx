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

/**
 * The daemon wraps every audit row with `type: "audit"` and spreads the row
 * fields, so the real semantic name lives in `ev.action`. Terminal events
 * (`task_failed` / `task_complete` / `task_blocked`) come through with the
 * status as the `type` and no `action` — fall back to `type` for those.
 */
function eventLabel(ev: TaskEvent): string {
  const action = (ev as { action?: unknown }).action;
  if (typeof action === 'string' && action) return action;
  return ev.type;
}

function rowTone(label: string): string {
  if (label === 'task_failed' || label.endsWith('_failure') || label.endsWith('_failed')) {
    return 'bg-tier-red-tint text-status-abandoned';
  }
  if (label === 'task_blocked' || label === 'escalated') {
    return 'bg-tier-yellow-tint text-status-blocked';
  }
  if (label === 'task_complete') return 'bg-tier-green-tint text-status-open';
  return 'text-fg';
}

export function TaskEventsLog({ taskId }: { taskId: string }): JSX.Element {
  const [events, setEvents] = useState<TaskEvent[]>([]);
  const [expanded, setExpanded] = useState<Set<string>>(new Set());
  const seen = useRef<Set<string>>(new Set());

  const append = useCallback((ev: TaskEvent) => {
    const sig = signature(ev);
    if (seen.current.has(sig)) return;
    seen.current.add(sig);
    setEvents((prev) => [...prev, ev]);
  }, []);

  useTaskTailSSE(taskId, append);

  const toggle = (sig: string) =>
    setExpanded((prev) => {
      const next = new Set(prev);
      if (next.has(sig)) next.delete(sig);
      else next.add(sig);
      return next;
    });

  if (events.length === 0) {
    return <p className="text-fg-muted text-xs">Waiting for events…</p>;
  }
  return (
    <ol className="space-y-1 text-xs">
      {events.map((ev) => {
        const sig = signature(ev);
        const label = eventLabel(ev);
        const isOpen = expanded.has(sig);
        const hasPayload = ev.payload && Object.keys(ev.payload).length > 0;
        return (
          <li key={sig}>
            <button
              type="button"
              onClick={() => hasPayload && toggle(sig)}
              className={`flex w-full items-baseline gap-2 rounded-sm px-1 py-0.5 text-left ${
                hasPayload ? 'hover:bg-bg-subtle cursor-pointer' : 'cursor-default'
              }`}
              aria-expanded={hasPayload ? isOpen : undefined}
            >
              <span className="text-fg-muted font-mono">{ev.timestamp}</span>
              <span
                className={`inline-block rounded-sm px-1.5 py-px font-mono font-semibold ${rowTone(label)}`}
              >
                {label}
              </span>
              {ev.agent && <span className="text-fg-muted">· {ev.agent}</span>}
              {hasPayload && (
                <span className="text-fg-muted ml-auto">{isOpen ? '▾' : '▸'}</span>
              )}
            </button>
            {hasPayload && isOpen && (
              <pre className="bg-bg-subtle text-fg-muted mt-1 ml-4 overflow-x-auto rounded-sm p-2 font-mono text-xs whitespace-pre-wrap">
                {JSON.stringify(ev.payload, null, 2)}
              </pre>
            )}
          </li>
        );
      })}
    </ol>
  );
}
