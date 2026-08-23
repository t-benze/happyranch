import type { ReplyDeliveryEntry } from '@/lib/api/types';
import { formatElapsed } from '@/lib/elapsed';

/**
 * Compact per-pair reply-delivery rows (GH-688 Phase 1 Slice C).
 *
 * Renders the STORE-PROJECTED pair state from the wire ``reply_delivery``
 * list — never inferred from per-message invocation rows, never fabricating
 * per-covered-message state. Three honest states:
 *
 *  - ``queued``         — one unstarted coalesced wake; NOT an active
 *                         subprocess (static dot, muted text).
 *  - ``running``        — one claimed in-flight reply with an immutable
 *                         inclusive range; ``started_at`` is the only
 *                         subprocess evidence.
 *  - ``retry_required`` — unacknowledged range with no active wake; a
 *                         diagnostic (last terminal reason where the store
 *                         recorded one), never rendered as typing.
 *
 * A fully-settled pair is omitted from the projection, so an empty list
 * renders nothing (callers hide the whole section).
 */
export function ReplyDeliveryStrip({
  entries,
  nowMs,
}: {
  entries: ReplyDeliveryEntry[];
  nowMs?: number;
}): JSX.Element | null {
  if (entries.length === 0) return null;
  const now = nowMs ?? Date.now();
  return (
    <ul className="space-y-1">
      {entries.map((e) => (
        <li key={e.agent_name} className="flex items-baseline gap-1.5">
          <span
            aria-hidden="true"
            className={`h-1.5 w-1.5 shrink-0 rounded-full ${dotClass(e.state)}`}
          />
          <span className="text-text-primary truncate font-mono text-xs">{e.agent_name}</span>
          <span className={`text-caption shrink-0 ${stateClass(e.state)}`}>
            {replyDeliveryCaption(e, now)}
          </span>
        </li>
      ))}
    </ul>
  );
}

/** One honest compact caption per pair state — shared by the rail strip and
 *  the transcript-tail live indicator so both surfaces agree verbatim. */
export function replyDeliveryCaption(
  e: ReplyDeliveryEntry,
  nowMs?: number,
): string {
  const range = `messages ${e.from_seq}–${e.through_seq}`;
  switch (e.state) {
    case 'queued':
      // queued wake covering N coalesced transcript rows; never a subprocess.
      return `${e.coalesced_message_count} messages coalesced · ${range}`;
    case 'running': {
      const elapsed = formatElapsed(e.started_at, nowMs ?? Date.now());
      return elapsed ? `replying ${elapsed} · ${range}` : `replying · ${range}`;
    }
    case 'retry_required': {
      const reason = e.last_terminal_reason
        ? ` · last: ${e.last_terminal_reason}`
        : '';
      return `retry required · ${range}${reason}`;
    }
  }
}

function dotClass(state: ReplyDeliveryEntry['state']): string {
  switch (state) {
    case 'queued':
      return 'bg-border-default';
    case 'running':
      return 'bg-info';
    case 'retry_required':
      return 'bg-attention';
  }
}

function stateClass(state: ReplyDeliveryEntry['state']): string {
  switch (state) {
    case 'queued':
      return 'text-text-muted';
    case 'running':
      return 'text-info';
    case 'retry_required':
      return 'text-attention-text';
  }
}
