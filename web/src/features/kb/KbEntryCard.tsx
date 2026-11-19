import { Link } from 'react-router-dom';
import { StatusBadge } from '@/design-system/patterns/StatusBadge';
import { TONE_CLASS, toneFor } from '@/design-system/patterns/semanticTone';
import { cn } from '@/lib/utils';
import type { KBEntry } from '@/lib/api/types';
import type { KBEntrySummary } from '@/hooks/kb';
import { useTranslation } from '@/hooks/i18n';
import { formatCountFor } from '@/lib/i18n';
import { relativeKbAge } from './strings';

type Density = 'comfortable' | 'compact';

/* ------------------------------------------------------------------ */
/*  File glyph — leads every entry card (KB-04)                         */
/* ------------------------------------------------------------------ */

function FileBadge({ className }: { className?: string }): JSX.Element {
  return (
    <svg
      className={cn('inline-block shrink-0', className)}
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



export interface KbEntryCardProps {
  entry: KBEntrySummary | KBEntry;
  snippet?: string;
  to: string;
  active?: boolean;
  density?: Density;
  /** Optional view-count from kb_views for the "viewed Nx (CLI)" label. */
  viewCount?: number;
}

export function KbEntryCard({
  entry,
  snippet,
  to,
  active,
  density = 'comfortable',
  viewCount,
}: KbEntryCardProps): JSX.Element {
  const { t, locale } = useTranslation();
  const pad = density === 'compact' ? 'p-2' : 'p-3';
  // THR-376 r2 K2: the 38px icon tile takes the entry type's tone fill (same
  // tile recipe as the Skills catalog); unknown/neutral types use the sunken
  // surface rather than the neutral badge's outline.
  const tone = toneFor(entry.type);
  const tileTint = tone === 'neutral' ? 'bg-surface-sunken text-text-secondary' : TONE_CLASS[tone];
  return (
    <Link
      to={to}
      className={cn(
        'border-border-default bg-surface-raised flex gap-3 rounded-lg border shadow-pasture-sm',
        pad,
        active && 'ring-accent-ring border-accent-default ring-2',
        'hover:bg-surface-raised/80 transition-shadow hover:shadow-pasture',
      )}
    >
      <div
        aria-hidden="true"
        className={cn('flex size-9.5 shrink-0 items-center justify-center rounded-md', tileTint)}
      >
        <FileBadge className="h-4.5 w-4.5" />
      </div>
      <div className="min-w-0 flex-1">
        {/* THR-376 r2 K2: badge row above the title — the semantic type badge
            (SOP green / reference blue / ruling amber via the shared tone map;
            other types neutral), then the slug and relative age in mono. */}
        <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
          <StatusBadge tone={tone} label={entry.type} />
          {/* nbsp keeps the separator on the slug's line when the row wraps. */}
          <span className="text-text-muted font-mono text-xs tabular-nums">
            {entry.slug}{'\u00a0· '}{relativeKbAge(entry.updated_at, locale, t)}
          </span>
        </div>
        {/* THR-376 r2 K2: row titles use Hanken 600 (serif is for h1/h2/display numbers only). */}
        <div className="text-text-primary text-body mt-1 font-semibold">{entry.title}</div>
        {snippet && <p className="text-text-muted mt-1.5 text-sm">{snippet}</p>}
        {/* THR-376 r2 K3: tags as one inline mono meta line (was one pill per
            tag). Display only — tag filtering lives in the rail. Tags are
            freeform, so a long unbroken tag breaks anywhere instead of
            overflowing the card (QA-1). */}
        {density === 'comfortable' && entry.tags.length > 0 && (
          <ul
            aria-label={t('kb.railTagsSection')}
            className="text-text-muted text-task-meta mt-1.5 font-mono wrap-anywhere"
          >
            {entry.tags.map((tag, i) => (
              <li key={tag} className="inline">
                {tag}
                {/* nbsp binds the separator to the preceding tag so a wrapped
                    line never starts with "·". */}
                {i < entry.tags.length - 1 && <span aria-hidden="true">{'\u00a0· '}</span>}
              </li>
            ))}
          </ul>
        )}
        {viewCount !== undefined && (
          <div className="text-text-muted mt-1.5 font-mono text-xs tabular-nums">
            {t('kb.viewedLabel', { count: viewCount, number: formatCountFor(locale, viewCount) })}
          </div>
        )}
      </div>
    </Link>
  );
}
