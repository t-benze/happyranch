/**
 * Real route builders — paths under `/orgs/:slug/threads/...`.
 *
 * Reads the active slug from `OrgSlugContext` (populated by `<OrgProvider>`
 * inside `routes.tsx`). Called by compositions through
 * `@/hooks/threads.useThreadRoutes()`.
 *
 * Uses the **optional** slug variant so layout chrome (`TopBar`) can call us
 * from above the `<OrgProvider>` boundary — e.g. on the `/` index route
 * before redirect — without throwing. When no slug is in scope, `inbox` and
 * `detail` return `'#'`, which renders the NavLink as inert.
 */
import { useOrgSlugOptional } from '@/lib/orgSlug';
import type { TasksRoutes, ThreadRoutes } from './DataContext';

export function useRealThreadRoutes(): ThreadRoutes {
  const slug = useOrgSlugOptional();
  return {
    detail: (threadId: string) => (slug ? `/orgs/${slug}/threads/${threadId}` : '#'),
    inbox: () => (slug ? `/orgs/${slug}/threads` : '#'),
    inboxForOrg: (target: string) => `/orgs/${target}/threads`,
  };
}

export function useRealTasksRoutes(): TasksRoutes {
  const slug = useOrgSlugOptional();
  return {
    detail: (taskId: string) => (slug ? `/orgs/${slug}/tasks/${taskId}` : '#'),
    inbox: () => (slug ? `/orgs/${slug}/tasks` : '#'),
    inboxForOrg: (target: string) => `/orgs/${target}/tasks`,
  };
}
