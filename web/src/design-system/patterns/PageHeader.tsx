/**
 * PageHeader — title (h2) + optional meta line + right-aligned actions slot.
 * Used by ThreadHeader and any future screen header. No data, no hooks.
 */
import type { ReactNode } from 'react';

interface PageHeaderProps {
  title: ReactNode;
  meta?: ReactNode;
  actions?: ReactNode;
}

export function PageHeader({ title, meta, actions }: PageHeaderProps): JSX.Element {
  return (
    <div className="flex items-start justify-between gap-3">
      <div className="min-w-0 flex-1">
        <h2 className="truncate text-h2 text-text-primary">{title}</h2>
        {meta && (
          <div className="mt-1 text-caption text-text-muted">{meta}</div>
        )}
      </div>
      {actions && (
        <div className="flex shrink-0 items-center gap-1">{actions}</div>
      )}
    </div>
  );
}

export const meta = {
  name: "PageHeader",
  layer: "pattern",
  import: "@/design-system/patterns/PageHeader",
  variants: {},
  consumes: ["typography.scale.h2", "typography.scale.caption"],
  example: "<PageHeader title='Threads' meta='12 open' />",
} as const;
