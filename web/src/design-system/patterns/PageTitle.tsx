/**
 * PageTitle — the title block of a top-level page (THR-376 Phase 3):
 * optional eyebrow, exactly one <h1>, optional lede, optional actions.
 *
 * Pasture `web/design/pasture-ds.css`: the eyebrow is `.eyebrow`
 * (`text-eyebrow` 11px / 600 / 0.07em, uppercase, ink-3 = `text-text-muted`),
 * the title is `[data-dir="a"] .h1` (`font-display` + `text-page-title`
 * 26px / 500 / -0.3px), the lede is body copy in `.muted` (ink-2 =
 * `text-text-secondary`). Actions sit to the right, top-aligned, and stack
 * below the text under `sm`.
 *
 * The page keeps its own outer wrapper (border, padding, margin below); this
 * pattern only owns the block itself. The Home greeting is NOT a PageTitle:
 * it keeps `text-display` (30px). No data, no hooks.
 */
import type { HTMLAttributes, ReactNode } from 'react';
import { cn } from '@/lib/utils';

interface PageTitleProps extends Omit<HTMLAttributes<HTMLDivElement>, 'title'> {
  /** The page's single <h1>. */
  title: ReactNode;
  /** Uppercase line above the title (counts, section name). */
  eyebrow?: ReactNode;
  /** One short line of body copy under the title. Phrasing content only. */
  lede?: ReactNode;
  /** Header controls aligned to the right of the text block. */
  actions?: ReactNode;
}

export function PageTitle({
  title,
  eyebrow,
  lede,
  actions,
  className,
  ...rest
}: PageTitleProps): JSX.Element {
  return (
    <div
      {...rest}
      className={cn(
        'flex flex-col items-start justify-between gap-3 sm:flex-row',
        className,
      )}
    >
      <div className="min-w-0 flex-1">
        {eyebrow && (
          <p className="text-eyebrow text-text-muted uppercase">{eyebrow}</p>
        )}
        {/* Plain strings, not cn(): tailwind-merge reads the custom
            `text-page-title` size as a text colour and would drop it in
            favour of `text-text-primary`. */}
        <h1
          className={`font-display text-page-title text-text-primary${
            eyebrow ? ' mt-1' : ''
          }`}
        >
          {title}
        </h1>
        {lede && (
          <p className="text-body text-text-secondary mt-1">{lede}</p>
        )}
      </div>
      {actions && (
        <div className="flex shrink-0 flex-wrap items-center gap-2">
          {actions}
        </div>
      )}
    </div>
  );
}
