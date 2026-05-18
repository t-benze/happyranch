/**
 * Placeholder shell. Future home of the task-graph surface — running,
 * blocked, completed, and revisited tasks across the org. Sketch in
 * `web/UI_SPEC.md` §8.
 */
import { EmptyState } from '@/design-system/patterns/EmptyState';

export function TasksPage(): JSX.Element {
  return (
    <EmptyState
      title="Tasks"
      body="The task graph — running, blocked, completed, and revisited tasks across the org. Coming soon."
    />
  );
}
