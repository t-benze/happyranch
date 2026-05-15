/**
 * Threads v2 prototype.
 *
 * Identity test for the prototype harness — this file imports the same
 * `ThreadsPage` composition the production `/orgs/:slug/threads` route
 * renders. The surrounding `<PrototypeProvider>` swaps the data layer
 * underneath. Zero JSX changes here; if you find yourself forking
 * `ThreadsPage`, the abstraction is wrong.
 *
 * Goal of the demo: prove that PR 3's harness keeps `ThreadsPage` the
 * single source of truth, while letting the designer agent iterate on it
 * under fixtures.
 */
import { ThreadsPage } from '@/features/threads/ThreadsPage';

export function ThreadsV2Screen(): JSX.Element {
  return <ThreadsPage />;
}
