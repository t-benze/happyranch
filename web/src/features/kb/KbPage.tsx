/**
 * Placeholder shell. Future home of the knowledge-base browser. Read-only
 * in v0.1; adds remain CLI-only. Sketch in `web/UI_SPEC.md` §9.
 */
import { EmptyState } from '@/design-system/patterns/EmptyState';

export function KbPage(): JSX.Element {
  return (
    <EmptyState
      title="Knowledge base"
      body="Browse and read KB entries. Read-only in v0.1; adds stay CLI-only. Coming soon."
    />
  );
}
