import type { ResponderStatusEntry, ThreadMessage } from '@/lib/api/types';

/**
 * In-flight responder plus the purpose of its wake, carried from the
 * authoritative `thread_invocations.purpose` on the wire (TASK-5553).
 *
 * Conversational REPLY wakes and special-purpose wakes (TASK_FOLLOWUP /
 * BOOTSTRAP) are distinguished by the WIRE purpose, never by the triggering
 * row's kind: a coalesced REPLY delivery range can anchor on a SYSTEM row
 * (its follow-on mint keys the first unacknowledged sequence, which may be a
 * system divider), so a kind-based classifier would mislabel that REPLY as a
 * special wake and fail to suppress it next to the store-projected pair row.
 * The transcript tail uses this to suppress an inferred row ONLY when it is a
 * conversational REPLY already owned by a live pair — never by agent identity
 * alone, so a same-agent special-purpose wake coexists with its REPLY pair.
 */
export type ResponderPurpose = ResponderStatusEntry['purpose'];

export interface InFlightResponder extends ResponderStatusEntry {
  purpose: ResponderPurpose;
}

/**
 * Agents with an in-flight reply obligation across a thread's messages —
 * `working` (mid-reply) or `queued` (waiting). Surfaced as inline TypingBubbles
 * at the transcript tail.
 *
 * Deduped by (agent_name, purpose). Because thread broadcast mints a fresh
 * invocation for every participant on every message, one agent can hold several
 * in-flight invocations at once (e.g. `working` on seq 1, `queued` on seq 2). A
 * `working` turn always wins: it carries the live elapsed state and must not be
 * masked by a later `queued` invocation. Among same-status entries, last-seen
 * wins. Purpose keeps a conversational REPLY and a special-purpose wake
 * (TASK_FOLLOWUP) for the SAME agent as separate rows — the store projection
 * owns the REPLY pair while the special-purpose wake stays inferred, and both
 * are allowed to coexist (Slice C reviewer finding).
 */
export function selectInFlightResponders(messages: ThreadMessage[]): InFlightResponder[] {
  const byAgentAndPurpose = new Map<string, InFlightResponder>();
  for (const m of messages) {
    for (const s of m.responder_status ?? []) {
      if (s.status !== 'working' && s.status !== 'queued') continue;
      // Authoritative wire purpose — never inferred from the triggering row kind.
      const purpose = s.purpose;
      const key = `${s.agent_name}\u0000${purpose}`;
      const existing = byAgentAndPurpose.get(key);
      if (existing?.status === 'working' && s.status === 'queued') continue;
      byAgentAndPurpose.set(key, { ...s, purpose });
    }
  }
  return [...byAgentAndPurpose.values()];
}
