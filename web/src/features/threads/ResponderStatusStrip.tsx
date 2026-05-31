import type { ResponderStatusEntry } from '@/lib/api/types';

export function ResponderStatusStrip({
  statuses,
}: {
  statuses: ResponderStatusEntry[];
}): JSX.Element | null {
  if (statuses.length === 0) return null;
  return (
    <div className="text-xs text-neutral-500 mt-1 flex flex-wrap gap-x-3">
      {statuses.map((s) => (
        <span key={s.agent_name}>
          <span className="font-medium">{s.agent_name}</span>:{' '}
          <span className={statusClass(s.status)}>{statusLabel(s.status)}</span>
        </span>
      ))}
    </div>
  );
}

function statusLabel(s: ResponderStatusEntry['status']): string {
  switch (s) {
    case 'pending':
      return 'pending…';
    case 'replied':
      return 'replied';
    case 'declined':
      return 'declined';
    case 'failed':
      return 'failed';
  }
}

function statusClass(s: ResponderStatusEntry['status']): string {
  switch (s) {
    case 'pending':
      return 'text-neutral-400';
    case 'replied':
      return 'text-emerald-600';
    case 'declined':
      return 'text-neutral-500';
    case 'failed':
      return 'text-amber-600';
  }
}
