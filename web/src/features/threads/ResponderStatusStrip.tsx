import type { ResponderStatusEntry } from '@/lib/api/types';
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
  // Light inline terminal record (THR-061 a-thread-detail — the founder found
  // the old carded "Responders · this dispatch" panel too heavy). No card, no
  // uppercase header: just a wrapped row of "· <agent> <state>" with a small
  // color-coded dot + colored state label per agent. The in-flight
  // working/queued states stay on the tail TypingBubble (no dup here).
  return (
    <div className="text-caption mt-1.5 flex flex-wrap items-center gap-x-3 gap-y-1">
      {terminal.map((s) => (
        <span key={s.agent_name} className="inline-flex items-center gap-1.5">
          <span aria-hidden="true" className={`h-1.5 w-1.5 shrink-0 rounded-full ${dotClass(s)}`} />
          <span className="text-text-secondary font-mono">{s.agent_name}</span>
          <span className={stateClass(s)}>{statusLabel(s, now)}</span>
        </span>
      ))}
    </div>
  );
}

// A founder-initiated abort is persisted as a `failed` invocation with
// decline_reason='founder_aborted' (the backend reap marker) — categorized
// as 'infra_fail'. But an abort is a deliberate cancellation, NOT an infra
// failure, so it must read as a NEUTRAL 'aborted' state, never red "reply
// failed…". Branch on this marker BEFORE the category/status switch. Genuine
// failures (any other decline_reason) keep their danger styling untouched.
function isAborted(s: ResponderStatusEntry): boolean {
  return s.decline_reason === 'founder_aborted';
}

function statusLabel(s: ResponderStatusEntry, nowMs: number): string {
  if (isAborted(s)) return 'aborted';
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

// Colored state LABEL token per terminal state (a-thread-detail). The replied
// (accent) and failed (danger) tokens are asserted by ResponderStatusStrip
// tests, so keep those class names stable.
function stateClass(s: ResponderStatusEntry): string {
  if (isAborted(s)) return 'text-text-muted';
  switch (s.status) {
    case 'queued':
      return 'text-text-muted';
    case 'working':
      return 'text-info';
    case 'replied':
      return 'text-accent-text';
    case 'declined':
      return 'text-attention-text';
    case 'failed':
      return 'text-danger';
  }
}

// Small leading dot color per terminal state (background token).
function dotClass(s: ResponderStatusEntry): string {
  if (isAborted(s)) return 'bg-border-default';
  switch (s.status) {
    case 'queued':
      return 'bg-border-default';
    case 'working':
      return 'bg-info';
    case 'replied':
      return 'bg-accent';
    case 'declined':
      return 'bg-attention';
    case 'failed':
      return 'bg-danger';
  }
}
