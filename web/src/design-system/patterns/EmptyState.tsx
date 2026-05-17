/**
 * EmptyState — centered icon + title + body + optional CTA. Per DESIGN.md
 * `components.empty_state`. Max width 28rem so the prose stays scannable.
 *
 * Pure prop-driven. The CTA uses our `Button` primitive.
 */
import type { ReactNode } from 'react';
import { Button } from '@/design-system/primitives/Button';

interface EmptyStateProps {
  icon?: ReactNode;
  title: string;
  body: ReactNode;
  cta?: { label: string; onClick: () => void };
}

export function EmptyState({ icon, title, body, cta }: EmptyStateProps): JSX.Element {
  return (
    <div className="mx-auto flex h-full max-w-[28rem] flex-col items-center justify-center px-4 py-12 text-center">
      {icon && (
        <div aria-hidden="true" className="mb-3 text-text-muted">
          {icon}
        </div>
      )}
      <h3 className="text-h3 text-text-secondary">{title}</h3>
      <div className="mt-2 text-body text-text-muted">{body}</div>
      {cta && (
        <div className="mt-5">
          <Button onClick={cta.onClick}>{cta.label}</Button>
        </div>
      )}
    </div>
  );
}

export const meta = {
  name: "EmptyState",
  layer: "pattern",
  import: "@/design-system/patterns/EmptyState",
  variants: {},
  consumes: ["components.empty_state"],
  example: "<EmptyState title='No threads' body='Start one with opc threads compose.' />",
} as const;
