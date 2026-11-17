/**
 * List / Row / RowAction — one card per group, rows inside it (THR-376 Phase 3b).
 *
 * Replaces "a stack of cards" lists (one bordered, shadowed card per item with
 * gaps between them) with a single Pasture `Card` whose rows are separated by
 * a 1px `--border` hairline (ds.css `.divider`: 1px, var(--border)).
 *
 * ds.css has no list-row rule. Row padding follows ds.css `.card-head`
 * (16px block / 20px inline = `py-4 px-5`) so rows line up with a CardHeader
 * and with the card's 20px padding. Interactive rows hover to `--surface-2`
 * (`bg-surface-hover`, the ds.css `.btn:hover` / `.org-opt:hover` fill).
 *
 *   <List aria-label="Jobs">
 *     <Row>static content</Row>
 *     <Row padding="none">
 *       <RowAction asChild><Link to="…">…</Link></RowAction>
 *     </Row>
 *   </List>
 *
 * The card clips its rows (`overflow-hidden`) so a hovered first/last row
 * follows the 18px corner. Pure props in, JSX out.
 */
import {
  Children,
  cloneElement,
  isValidElement,
  type ButtonHTMLAttributes,
  type HTMLAttributes,
  type LiHTMLAttributes,
  type ReactElement,
} from 'react';

import { Card } from '@/design-system/primitives/Card';
import { cn } from '@/lib/utils';

export interface ListProps extends HTMLAttributes<HTMLUListElement> {
  /** Card radius: `container` 18px (default) or `entry` 12px. */
  variant?: 'container' | 'entry';
  /** Classes for the outer card. */
  className?: string;
}

export function List({ variant = 'container', className, children, ...rest }: ListProps): JSX.Element {
  return (
    <Card variant={variant} padding="none" className={cn('overflow-hidden', className)}>
      <ul {...rest} className="divide-border-default divide-y">
        {children}
      </ul>
    </Card>
  );
}

export interface RowProps extends LiHTMLAttributes<HTMLLIElement> {
  /** `default` pads the row 16px / 20px; `none` when a RowAction fills the row. */
  padding?: 'default' | 'none';
}

export function Row({ padding = 'default', className, ...rest }: RowProps): JSX.Element {
  return <li {...rest} className={cn(padding === 'default' && 'px-5 py-4', className)} />;
}

const ROW_ACTION = 'w-full px-5 py-4 text-left transition-colors hover:bg-surface-hover';

export interface RowActionProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  /** Merge the row-action classes into the single child (e.g. a router Link) instead of rendering a button. */
  asChild?: boolean;
}

/**
 * The clickable surface of a row: full width, row padding, hover fill.
 * Consumer classes win over the defaults (tailwind-merge), so a dense list can
 * pass its own padding. With `asChild`, the child keeps its own props and
 * element; only `className` is merged.
 */
export function RowAction({ asChild = false, className, children, type, ...rest }: RowActionProps): JSX.Element {
  if (asChild) {
    const child = Children.only(children);
    if (isValidElement(child)) {
      const element = child as ReactElement<{ className?: string }>;
      return cloneElement(element, {
        className: cn(ROW_ACTION, className, element.props.className),
      });
    }
  }
  return (
    <button {...rest} type={type ?? 'button'} className={cn(ROW_ACTION, className)}>
      {children}
    </button>
  );
}
