import { Link } from 'react-router-dom';
import { cn } from '@/lib/utils';
import type { KBEntry } from '@/lib/api/types';
import { KB_STRINGS } from './strings';

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
  /** Optional view-count from kb_views for the "viewed Nx (CLI)" label. */
  viewCount?: number;
}

export function KbEntryCard({
  entry,
  to,
  active,
  density = 'comfortable',
  viewCount,
}: KbEntryCardProps): JSX.Element {
  const pad = density === 'compact' ? 'p-2' : 'p-3';
  return (
    <Link
      to={to}
      className={cn(
        'border-border-default bg-surface-raised block rounded-lg border shadow-pasture-sm',
        pad,
        active && 'ring-accent-ring border-accent-default ring-2',
        'hover:bg-surface-raised/80 transition-shadow hover:shadow-pasture',
      )}
    >
      <div className="text-text-muted font-mono text-xs tabular-nums">{entry.slug}</div>
      <div className="text-text-primary mt-0.5 flex items-baseline gap-2 flex-wrap">
        <span className="font-display font-medium">{entry.title}</span>
        <span className="text-text-muted text-xs">· {entry.type}</span>
        <span className="text-text-muted text-xs font-mono tabular-nums">· {relativeAge(entry.updated_at)}</span>
      </div>
      {density === 'comfortable' && entry.tags.length > 0 && (
        <div className="mt-1.5 flex flex-wrap gap-1.5">
          {entry.tags.map((t) => (
            <span key={t} className="inline-flex items-center rounded-full bg-surface-sunken px-2 py-0.5 text-xs text-text-muted border border-border-subtle">
              {t}
            </span>
          ))}
        </div>
      )}
      {viewCount !== undefined && (
        <div className="text-text-muted mt-1.5 text-xs font-mono tabular-nums">
          {KB_STRINGS.viewedLabel(viewCount)}
        </div>
      )}
    </Link>
  );
}
