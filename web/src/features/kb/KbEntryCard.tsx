import { Link } from 'react-router-dom';
import { toneClass } from '@/design-system/patterns/semanticTone';
import { cn } from '@/lib/utils';
import type { KBEntry } from '@/lib/api/types';
import { KB_STRINGS } from './strings';

type Density = 'comfortable' | 'compact';

/* ------------------------------------------------------------------ */
/*  File glyph — leads every entry card (KB-04)                         */
/* ------------------------------------------------------------------ */

function FileBadge({ className }: { className?: string }): JSX.Element {
  return (
    <svg
      className={cn('text-text-muted inline-block shrink-0', className)}
      width="14"
      height="14"
      viewBox="0 0 24 24"
      fill="currentColor"
      aria-hidden="true"
    >
      <path d="M6 2.75A1.75 1.75 0 0 0 4.25 4.5v15A1.75 1.75 0 0 0 6 21.25h12A1.75 1.75 0 0 0 19.75 19.5V9h-5.25A1.25 1.25 0 0 1 13.25 7.75V2.75H6Zm8.75.31V7.5h4.44L14.75 3.06Z" />
    </svg>
  );
}

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
      <div className="flex items-center gap-1.5">
        <FileBadge className="h-3 w-3" />
        <span className="text-text-muted font-mono text-xs tabular-nums">{entry.slug}</span>
      </div>
      <div className="text-text-primary mt-0.5 flex flex-wrap items-baseline gap-2">
        <span className="font-display font-medium">{entry.title}</span>
        {/* Semantic type badge — SOP green / reference blue / ruling amber via
            the shared colour map (THR-099 Batch 1 proof-of-render). Other
            types fall back to the neutral grey tone. */}
        <span
          className={cn(
            'inline-flex items-center rounded-full px-2 py-0.5 text-2xs font-semibold uppercase tracking-wide',
            toneClass(entry.type),
          )}
        >
          {entry.type}
        </span>
        <span className="text-text-muted font-mono text-xs tabular-nums">· {relativeAge(entry.updated_at)}</span>
      </div>
      {density === 'comfortable' && entry.tags.length > 0 && (
        <div className="mt-1.5 flex flex-wrap gap-1.5">
          {entry.tags.map((t) => (
            <span key={t} className="bg-surface-sunken text-text-muted border-border-subtle inline-flex items-center rounded-full border px-2 py-0.5 text-xs">
              {t}
            </span>
          ))}
        </div>
      )}
      {viewCount !== undefined && (
        <div className="text-text-muted mt-1.5 font-mono text-xs tabular-nums">
          {KB_STRINGS.viewedLabel(viewCount)}
        </div>
      )}
    </Link>
  );
}
