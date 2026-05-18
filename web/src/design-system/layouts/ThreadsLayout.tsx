/**
 * ThreadsLayout — two-pane grid that hosts the threads feature.
 *
 * Mirrors DESIGN.md `layout.grid.threads_page` (cols: "340px 1fr"; gap 0).
 * Lives in /layouts/ because the arbitrary track widths are a layout
 * primitive, not a feature-level styling choice — keeping the grid here
 * lets `tailwindcss/no-arbitrary-value` stay error-level on
 * `src/features/**`.
 *
 * Note on width: DESIGN.md specifies 340px, but the current production
 * page renders at 320px (set in PR 2). The narrower column is a
 * pre-existing divergence retained to keep PR 5 a zero-pixel-diff change;
 * a future PR can flip the constant alongside any token-rename pass.
 */
import type { ReactNode } from 'react';

interface ThreadsLayoutProps {
  /** Left pane — the inbox column. */
  inbox: ReactNode;
  /** Right pane — the active-thread detail column or the empty state. */
  detail: ReactNode;
}

export function ThreadsLayout({ inbox, detail }: ThreadsLayoutProps): JSX.Element {
  return (
    <div className="grid h-full grid-cols-[320px_1fr] grid-rows-[minmax(0,1fr)]">
      {inbox}
      {detail}
    </div>
  );
}

export const meta = {
  name: "ThreadsLayout",
  layer: "layout",
  import: "@/design-system/layouts/ThreadsLayout",
  variants: {},
  consumes: ["layout.grid.threads_page"],
  example: "<ThreadsLayout inbox={<aside />} detail={<section />} />",
} as const;
