/**
 * ContentWrap — Direction-A self-scrolling, centered content column.
 *
 * Mirrors ds.css `.content` + `.wrap` (a-*.html Direction-A bundle):
 *   .content { flex: 1; overflow-y: auto }         → the scroll surface
 *   .wrap    { padding: 26px; max-width: 1180px; margin: 0 auto }
 *   @media (max-width: 860px) { .wrap { padding: 18px 16px } }  → mobile collapse
 *
 * The 1180 cap lives in the token registry as `--spacing-content` (tokens.css),
 * surfaced here via the `max-w-content` utility — not hard-coded per page.
 *
 * Applied INSIDE each `.wrap`-family page component (replacing its own outer
 * scroll+cap div), never at the route level — this keeps routes.tsx/AppShell
 * untouched and lets full-height own-inner pages (agents/kb/thread-detail) opt
 * out. Shared from `@/design-system` because the web eslint
 * `no-restricted-imports` rule forbids cross-feature imports (THR-099 plan §4).
 */
import type { ReactNode } from 'react';
import { cn } from '@/lib/utils';

export interface ContentWrapProps {
  children: ReactNode;
  /** Extra classes for the inner centered column (the `.wrap`). */
  className?: string;
}

export function ContentWrap({ children, className }: ContentWrapProps): JSX.Element {
  return (
    <div className="h-full overflow-y-auto">
      <div className={cn('mx-auto max-w-content px-4 py-[18px] sm:p-[26px]', className)}>
        {children}
      </div>
    </div>
  );
}

export const meta = {
  name: 'ContentWrap',
  layer: 'layout',
  import: '@/design-system/layouts/ContentWrap/ContentWrap',
  variants: {},
  consumes: ['layout.content', 'layout.wrap'],
  example: '<ContentWrap>{children}</ContentWrap>',
} as const;
