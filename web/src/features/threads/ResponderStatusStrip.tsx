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
  // Carded "Responders · this dispatch" strip (THR-061 a-thread-detail mockup).
  // Per-agent terminal record with a category note + a color-coded pill. The
  // in-flight working/queued states stay on the tail TypingBubble (no dup here).
  return (
    <div className="border-border-subtle bg-surface-sunken mt-2 max-w-md overflow-hidden rounded-lg border">
      <div className="text-text-muted border-border-subtle text-overline border-b px-3 py-2 font-semibold tracking-wide uppercase">
        Responders · this dispatch
      </div>
      <ul>
        {terminal.map((s) => (
          <li
            key={s.agent_name}
            className="border-border-subtle flex items-center gap-2 border-t px-3 py-2 text-xs first:border-t-0"
          >
            <span className="text-text-primary font-mono text-xs">{s.agent_name}</span>
            {(s.status === 'declined' || s.status === 'failed') && s.category && (
              <span className="text-text-muted text-caption font-mono">category: {s.category}</span>
            )}
            <span
              className={`text-overline ml-auto inline-flex shrink-0 items-center gap-1 rounded-full px-2 py-0.5 font-bold tracking-wide uppercase ${pillClass(s.status)}`}
            >
              {s.status === 'replied' && (
                <svg width="9" height="9" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="3" aria-hidden="true">
                  <path d="M20 6L9 17l-5-5" />
                </svg>
              )}
              {statusLabel(s, now)}
            </span>
          </li>
        ))}
      </ul>
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

// Pill palette for the carded strip — maps each terminal state to a design
// token soft/foreground pair (mirrors the a-thread-detail resp-pill styles).
function pillClass(s: ResponderStatus): string {
  switch (s) {
    case 'queued':
      return 'bg-surface border-border-default text-text-muted border';
    case 'working':
      return 'bg-info-soft text-info';
    case 'replied':
      return 'bg-accent-soft text-accent-text';
    case 'declined':
      return 'bg-attention-soft text-attention-text';
    case 'failed':
      return 'bg-danger-soft text-danger';
  }
}
