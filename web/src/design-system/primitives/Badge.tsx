/**
 * Badge — a small static pill for a state or a short qualifier (Active,
 * Archived, Not yet used, a duplicate-name warning). Colour comes only from
 * the shared `semanticTone` vocabulary: callers pass `TONE_CLASS[tone]` from
 * `@/design-system/patterns/semanticTone` as `toneClass` (primitives may not
 * import patterns, so the tone map stays the single source of truth there).
 * The caller also supplies the localized word, so the primitive owns no copy
 * and no status mapping.
 *
 * Use `StatusBadge` for task/thread lifecycle statuses; use `Badge` for a
 * feature's own state words (THR-390 PR6, EM-approved primitive).
 *
 * Pure prop-driven. Not interactive: it is never a button or a link.
 */
import type { ReactNode } from 'react';
import { cn } from '@/lib/utils';

export interface BadgeProps {
  /** Token classes for the tone: `TONE_CLASS[tone]` from `semanticTone`. */
  toneClass: string;
  children: ReactNode;
  /** Decorative leading icon; give it `aria-hidden`. */
  icon?: ReactNode;
  className?: string;
}

const BADGE_CLASS =
  'font-sans inline-flex max-w-full items-center gap-1 rounded-full px-2 py-0.5 text-xs font-medium whitespace-nowrap';

export function Badge({ toneClass, children, icon, className }: BadgeProps): JSX.Element {
  return (
    <span className={cn(BADGE_CLASS, toneClass, className)}>
      {icon}
      <span className="min-w-0 truncate">{children}</span>
    </span>
  );
}
