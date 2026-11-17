/**
 * Card + CardHeader — the Pasture card surface (THR-376 Phase 3b).
 *
 * Pasture `web/design/pasture-ds.css` (Direction A):
 *   .card      { background: var(--surface); border: 1px solid var(--border);
 *                border-radius: var(--radius-lg); box-shadow: var(--shadow-sm); }
 *   .card-pad  { padding: 20px; }
 *   .card-head { display: flex; align-items: center; gap: 10px;
 *                padding: 16px 20px; border-bottom: 1px solid var(--border); }
 *   .card-title{ font-size: 13px; font-weight: 600; letter-spacing: -0.1px; }
 *                (line-height is not set, so it inherits body 1.5 = 19.5px)
 *   .card-head .spacer { flex: 1 1 auto; }
 *
 * Token mapping: --surface = `bg-surface-raised`, --border =
 * `border-border-default`, --shadow-sm = `shadow-pasture-sm` (the warm shadow;
 * Direction A keeps the same value in dark), --radius-lg 18px = `rounded-lg`,
 * --radius 12px = `rounded-md`, 20px = `p-5`.
 *
 * Variants:
 *   container (default) — a page section that holds content or a list: 18px.
 *   entry               — one item in a feed/grid of separate cards: 12px.
 *     ds.css has only `.card` (18px); the 12px entry radius comes from the
 *     designer review (THR-376 Phase 3 item 1) and reuses `--radius` 12px.
 *
 * `padding="none"` drops the 20px card padding for cards whose children own
 * their padding (a CardHeader, a List of Rows). Pure props in, JSX out.
 */
import { cva, type VariantProps } from 'class-variance-authority';
import type { HTMLAttributes, ReactNode } from 'react';

import { cn } from '@/lib/utils';

const cardVariants = cva(
  'bg-surface-raised border-border-default shadow-pasture-sm border',
  {
    variants: {
      variant: {
        container: 'rounded-lg',
        entry: 'rounded-md',
      },
      padding: {
        default: 'p-5',
        none: '',
      },
    },
    defaultVariants: {
      variant: 'container',
      padding: 'default',
    },
  },
);

type CardElement = 'div' | 'section' | 'article' | 'aside';

export interface CardProps
  extends HTMLAttributes<HTMLElement>,
    VariantProps<typeof cardVariants> {
  /** Rendered element. Use `section`/`article` when the card is a landmark or an item. */
  as?: CardElement;
}

export function Card({
  as: Element = 'div',
  variant,
  padding,
  className,
  ...rest
}: CardProps): JSX.Element {
  return (
    <Element {...rest} className={cn(cardVariants({ variant, padding }), className)} />
  );
}

type CardTitleElement = 'h2' | 'h3' | 'h4' | 'div';

export interface CardHeaderProps
  extends Omit<HTMLAttributes<HTMLDivElement>, 'title'> {
  /** The card title (`.card-title`: 13px / 600 / -0.1px). */
  title: ReactNode;
  /** Element for the title. Defaults to `h3`; use `div` when the title is not a heading. */
  titleAs?: CardTitleElement;
  /** Secondary text beside the title (counts, timestamps). */
  meta?: ReactNode;
  /** Controls pushed to the right edge (`.card-head .spacer`). */
  actions?: ReactNode;
}

export function CardHeader({
  title,
  titleAs: Title = 'h3',
  meta,
  actions,
  className,
  ...rest
}: CardHeaderProps): JSX.Element {
  return (
    <div
      {...rest}
      className={cn(
        'border-border-default flex items-center gap-2.5 border-b px-5 py-4',
        className,
      )}
    >
      {/* Plain string, not cn(): tailwind-merge reads the custom `text-body-sm`
          size as a text colour and would drop it next to `text-text-primary`.
          Title line-height is the `text-body-sm` token's 20px (1.25rem), not
          the 19.5px ds.css `.card-title` inherits from body 1.5. */}
      <Title className="text-body-sm text-text-primary min-w-0 truncate font-semibold tracking-[-0.1px]">
        {title}
      </Title>
      {meta && <span className="text-caption text-text-muted shrink-0">{meta}</span>}
      {actions && (
        <div className="ml-auto flex shrink-0 items-center gap-2">{actions}</div>
      )}
    </div>
  );
}
