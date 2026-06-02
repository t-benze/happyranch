import type { ThreadMessage } from '@/lib/api/types';
import { formatElapsed } from './ResponderStatusStrip';

export function ThreadActivityFooter({
  messages,
  nowMs,
}: {
  messages: ThreadMessage[];
  nowMs?: number;
}): JSX.Element | null {
  const now = nowMs ?? Date.now();
  // One working entry per agent (an agent has at most one in-flight turn per thread).
  const working = new Map<string, string | null>();
  for (const m of messages) {
    for (const s of m.responder_status ?? []) {
      if (s.status === 'working') working.set(s.agent_name, s.started_at);
    }
  }
  if (working.size === 0) return null;

  const names = [...working.keys()];
  const label =
    names.length === 1
      ? `${names[0]} is working on a reply… (${formatElapsed(working.get(names[0]) ?? null, now)})`
      : `${names.join(', ')} working…`;

  return (
    <div className="flex items-center gap-2 border-t border-neutral-200 px-4 py-2 text-xs text-neutral-500">
      <span className="inline-block h-2 w-2 animate-pulse rounded-full bg-sky-500" aria-hidden />
      <span>{label}</span>
    </div>
  );
}
