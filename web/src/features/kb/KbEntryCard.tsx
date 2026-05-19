import { Link } from 'react-router-dom';
import { cn } from '@/lib/utils';
import type { KBEntry } from '@/lib/api/types';

type Density = 'comfortable' | 'compact';

function relativeAge(iso: string): string {
  const ms = Date.now() - new Date(iso).getTime();
  const min = Math.round(ms / 60000);
  if (min < 1) return 'just now';
  if (min < 60) return `${min}m`;
  const hr = Math.round(min / 60);
  if (hr < 24) return `${hr}h`;
  const d = Math.round(hr / 24);
  return `${d}d`;
}

export interface KbEntryCardProps {
  entry: KBEntry;
  to: string;
  active?: boolean;
  density?: Density;
}

export function KbEntryCard({
  entry,
  to,
  active,
  density = 'comfortable',
}: KbEntryCardProps): JSX.Element {
  const pad = density === 'compact' ? 'p-2' : 'p-3';
  return (
    <Link
      to={to}
      className={cn(
        'border-border-subtle bg-surface-raised block rounded-lg border',
        pad,
        active && 'ring-accent ring-2',
        'hover:bg-surface-raised/80',
      )}
    >
      <div className="text-fg-muted font-mono text-xs">{entry.slug}</div>
      <div className="text-fg mt-0.5 flex items-baseline gap-2">
        <span className="font-medium">{entry.title}</span>
        <span className="text-fg-muted text-xs">· {entry.type}</span>
        <span className="text-fg-muted text-xs">· {relativeAge(entry.updated_at)}</span>
      </div>
      {density === 'comfortable' && entry.tags.length > 0 && (
        <div className="text-fg-muted mt-1 text-xs">{entry.tags.join(' · ')}</div>
      )}
    </Link>
  );
}
