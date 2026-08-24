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
  const running = entries.filter((entry) => entry.state === 'running');
  const queued = entries.filter((entry) => entry.state === 'queued');
  const diagnostics = entries.filter((entry) => entry.state === 'retry_required');
  return (
    <div className="space-y-2">
      {entries.length > 1 && (
        <p className="text-text-disabled text-caption tabular-nums">
          {entries.length} current deliveries
        </p>
      )}

      {running.length > 0 && (
        <ul aria-label="Active reply deliveries" className="space-y-1.5">
          {running.map((entry) => <DeliveryRow key={entry.agent_name} entry={entry} nowMs={now} />)}
        </ul>
      )}

      {diagnostics.length > 0 && (
        <ul aria-label="Reply delivery diagnostics" className="space-y-1.5">
          {diagnostics.map((entry) => <DeliveryRow key={entry.agent_name} entry={entry} nowMs={now} />)}
        </ul>
      )}

      {queued.length > 0 && (
        <details
          aria-label={`${queued.length} queued ${queued.length === 1 ? 'delivery' : 'deliveries'}`}
          className="border-border-default rounded-md border"
        >
          <summary className="text-text-secondary hover:bg-surface-raised marker:text-text-muted cursor-pointer rounded-md px-2 py-1.5 text-xs font-medium focus-visible:outline-2 focus-visible:outline-offset-2">
            {queued.length} queued {queued.length === 1 ? 'delivery' : 'deliveries'}
          </summary>
          <ul aria-label="Queued reply delivery details" className="border-border-default space-y-1.5 border-t px-2 py-2">
            {queued.map((entry) => <DeliveryRow key={entry.agent_name} entry={entry} nowMs={now} />)}
          </ul>
        </details>
      )}
    </div>
  );
}

function DeliveryRow({ entry, nowMs }: { entry: ReplyDeliveryEntry; nowMs: number }): JSX.Element {
  return (
    <li className="flex min-w-0 items-start gap-1.5">
      <span aria-hidden="true" className={`mt-1 h-1.5 w-1.5 rounded-full ${dotClass(entry.state)}`} />
      <span className="min-w-0">
        <span className="text-text-primary block font-mono text-xs leading-tight break-all">
          {entry.agent_name}
        </span>
        <span className={`text-caption block leading-snug break-words ${stateClass(entry.state)}`}>
          {replyDeliveryCaption(entry, nowMs)}
        </span>
      </span>
    </li>
  );
}

/** One honest compact caption per pair state — shared by the rail strip and
 *  the transcript-tail live indicator so both surfaces agree verbatim. */
export function replyDeliveryCaption(
  e: ReplyDeliveryEntry,
  nowMs?: number,
): string {
  const singleMessage = e.from_seq === e.through_seq;
  const range = singleMessage ? `message ${e.from_seq}` : `messages ${e.from_seq}–${e.through_seq}`;
  switch (e.state) {
    case 'queued':
      // queued wake covering N coalesced transcript rows; never a subprocess.
      return `${e.coalesced_message_count} ${e.coalesced_message_count === 1 ? 'message' : 'messages'} coalesced · ${range}`;
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
