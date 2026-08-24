import { useId, useState } from 'react';
import type { ReplyDeliveryEntry } from '@/lib/api/types';
import { formatElapsed } from '@/lib/elapsed';

/**
 * Compact per-pair reply-delivery rows (GH-688 Phase 1 Slice C; TASK-5553).
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
 * Presentation hierarchy (TASK-5553 founder redesign): RUNNING deliveries are
 * prioritized to the top and keep their full caption inline (replying + live
 * elapsed + inclusive range) — active work is the point of the rail. Queued
 * and retry_required rows collapse to one compact identity+state line with a
 * keyboard/screen-reader-accessible disclosure ("Details") that expands the
 * per-arrival coalescing detail (count, inclusive range, last terminal reason)
 * — so redundant settled/coalescing visual history stays collapsed until
 * inspected. Agent identity is never truncated (no indistinguishable
 * prefixes), and every pair stays individually visible: genuine concurrent
 * work is never merged or hidden.
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
  // RUNNING first (active deliveries), then queued, then retry_required;
  // stable by agent_name within a group.
  const ordered = [...entries].sort(
    (a, b) => statePriority(a.state) - statePriority(b.state) ||
      a.agent_name.localeCompare(b.agent_name),
  );
  return (
    <ul className="space-y-1">
      {ordered.map((e) => (
        <ReplyDeliveryRow key={e.agent_name} entry={e} now={now} />
      ))}
    </ul>
  );
}

function ReplyDeliveryRow({
  entry,
  now,
}: {
  entry: ReplyDeliveryEntry;
  now: number;
}): JSX.Element {
  const [open, setOpen] = useState(false);
  const detailsId = useId();
  const running = entry.state === 'running';
  return (
    <li className="min-w-0">
      <div className="flex items-baseline gap-1.5">
        <span
          aria-hidden="true"
          className={`h-1.5 w-1.5 shrink-0 self-center rounded-full ${dotClass(entry.state)}`}
        />
        {/* Full agent identity — never truncated, so distinct agent names stay
            distinguishable at any rail width. */}
        <span
          className={`min-w-0 flex-1 font-mono text-xs ${
            running ? 'text-text-primary font-semibold' : 'text-text-secondary'
          }`}
        >
          {entry.agent_name}
        </span>
        {running ? (
          <span className={`text-caption shrink-0 ${stateClass(entry.state)}`}>
            {replyDeliveryCaption(entry, now)}
          </span>
        ) : (
          <>
            <span className={`text-caption shrink-0 ${stateClass(entry.state)}`}>
              {shortStateLabel(entry.state)}
            </span>
            <button
              type="button"
              aria-expanded={open}
              aria-controls={detailsId}
              aria-label={`${open ? 'Hide' : 'Show'} ${entry.agent_name} reply delivery details`}
              onClick={() => setOpen((v) => !v)}
              className="text-text-muted hover:text-text-primary hover:bg-surface-hover text-overline shrink-0 rounded px-1 py-0.5 transition-colors"
            >
              {open ? 'Hide details' : 'Details'}
            </button>
          </>
        )}
      </div>
      {!running && open && (
        <div
          id={detailsId}
          className="text-caption text-text-muted mt-0.5 pl-3"
        >
          {replyDeliveryCaption(entry, now)}
        </div>
      )}
    </li>
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

/** Compact one-line state word for collapsed rows (queued / retry_required).
 *  RUNNING rows keep the full caption inline (active delivery priority). */
function shortStateLabel(state: ReplyDeliveryEntry['state']): string {
  switch (state) {
    case 'queued':
      return 'queued';
    case 'running':
      return 'replying';
    case 'retry_required':
      return 'retry required';
  }
}

function statePriority(state: ReplyDeliveryEntry['state']): number {
  switch (state) {
    case 'running':
      return 0;
    case 'queued':
      return 1;
    case 'retry_required':
      return 2;
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
