import type { ResponderStatus, ResponderStatusEntry } from '@/lib/api/types';
import { formatElapsed } from '@/lib/elapsed';

export function ResponderStatusStrip({
  statuses,
  nowMs,
}: {
  statuses: ResponderStatusEntry[];
  nowMs?: number;
}): JSX.Element | null {
  // In-flight states (queued/working) are surfaced by the inline TypingBubble
  // at the transcript tail; this strip is the per-message terminal record only.
  const terminal = statuses.filter(
    (s) => s.status === 'replied' || s.status === 'declined' || s.status === 'failed',
  );
  if (terminal.length === 0) return null;
  const now = nowMs ?? Date.now();
  return (
    <div className="mt-1 flex flex-wrap gap-x-3 text-xs text-neutral-500">
      {terminal.map((s) => (
        <span key={s.agent_name}>
          <span className="font-medium">{s.agent_name}</span>:{' '}
          <span className={statusClass(s.status)}>{statusLabel(s, now)}</span>
        </span>
      ))}
    </div>
  );
}

function statusLabel(s: ResponderStatusEntry, nowMs: number): string {
  switch (s.status) {
    case 'queued':
      return 'queued';
    case 'working': {
      const e = formatElapsed(s.started_at, nowMs);
      return e ? `working ${e}` : 'working…';
    }
    case 'replied':
      return 'replied';
    case 'declined':
    case 'failed':
      // Category-distinguished so the founder can tell the four terminal
      // causes apart. Falls back to today's generic label when category is
      // null (older/replied data or a row PR-A didn't classify).
      return terminalLabel(s) ?? s.status;
  }
}

// Maps the persisted failure/decline category to a founder-readable label.
// Returns null when there is no category to distinguish (caller falls back
// to the generic 'declined'/'failed' label).
function terminalLabel(s: ResponderStatusEntry): string | null {
  switch (s.category) {
    case 'declined':
      return 'declined';
    case 'no_callback':
      return 'reply failed (no callback)';
    case 'no_callback_after_reprompt':
      return 'reply failed (no callback after re-prompt)';
    case 'infra_fail': {
      // Surface the return code when the backend embedded one (rc=N), matching
      // the daemon's own infra-signature parse; otherwise a bare infra label.
      const rc = s.decline_reason?.match(/rc=(\d+)/i)?.[1];
      return rc ? `reply failed (infra: rc=${rc})` : 'reply failed (infra)';
    }
    default:
      return null;
  }
}

function statusClass(s: ResponderStatus): string {
  switch (s) {
    case 'queued':
      return 'text-neutral-400';
    case 'working':
      return 'text-sky-600';
    case 'replied':
      return 'text-emerald-600';
    case 'declined':
      return 'text-neutral-500';
    case 'failed':
      return 'text-amber-600';
  }
}
