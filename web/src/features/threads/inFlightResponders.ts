import type { ResponderStatusEntry, ThreadMessage } from '@/lib/api/types';

/**
 * Agents with an in-flight reply obligation across a thread's messages —
 * `working` (mid-reply) or `queued` (waiting). Surfaced as inline TypingBubbles
 * at the transcript tail.
 *
 * Deduped by agent name. Because thread broadcast mints a fresh invocation for
 * every participant on every message, one agent can hold several in-flight
 * invocations at once (e.g. `working` on seq 1, `queued` on seq 2). A `working`
 * turn always wins: it carries the live elapsed state and must not be masked by
 * a later `queued` invocation. Among same-status entries, last-seen wins.
 */
export function selectInFlightResponders(messages: ThreadMessage[]): ResponderStatusEntry[] {
  const byAgent = new Map<string, ResponderStatusEntry>();
  for (const m of messages) {
    for (const s of m.responder_status ?? []) {
      if (s.status !== 'working' && s.status !== 'queued') continue;
      const existing = byAgent.get(s.agent_name);
      if (existing?.status === 'working' && s.status === 'queued') continue;
      byAgent.set(s.agent_name, s);
    }
  }
  return [...byAgent.values()];
}
