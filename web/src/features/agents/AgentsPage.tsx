/**
 * Placeholder shell. Future home of the agent scorecard surface —
 * 30-day rolling acceptance / revise / error counts per agent, with a
 * tier color (green/yellow/red) earning its rent here. Sketch in
 * `web/UI_SPEC.md` §11.
 */
import { EmptyState } from '@/design-system/patterns/EmptyState';

export function AgentsPage(): JSX.Element {
  return (
    <EmptyState
      title="Agents"
      body="Agent scorecards — 30-day rolling acceptance, revise, and error counts. Coming soon."
    />
  );
}
