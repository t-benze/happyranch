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
      return 'declined';
    case 'failed':
      return 'failed';
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
