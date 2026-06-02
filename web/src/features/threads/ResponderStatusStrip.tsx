import type { ResponderStatus, ResponderStatusEntry } from '@/lib/api/types';

export function formatElapsed(startedAt: string | null, nowMs: number): string {
  if (!startedAt) return '';
  const secs = Math.max(0, Math.floor((nowMs - Date.parse(startedAt)) / 1000));
  if (secs < 60) return `${secs}s`;
  return `${Math.floor(secs / 60)}m`;
}

export function ResponderStatusStrip({
  statuses,
  nowMs,
}: {
  statuses: ResponderStatusEntry[];
  nowMs?: number;
}): JSX.Element | null {
  if (statuses.length === 0) return null;
  const now = nowMs ?? Date.now();
  return (
    <div className="text-xs text-neutral-500 mt-1 flex flex-wrap gap-x-3">
      {statuses.map((s) => (
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
