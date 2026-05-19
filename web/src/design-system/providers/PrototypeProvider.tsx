/**
 * Designer-sandbox data provider.
 *
 * Wraps:
 *   1. Its OWN `QueryClient` — prototypes never share cache with the
 *      production `/orgs/:slug/...` routes mounted under `<AppProvider>`.
 *   2. `<DataContext>` pointing every domain hook at the in-memory mock
 *      implementations in `_mock-threads.ts`.
 *   3. `<StaticOrgProvider slug="demo-org">` so compositions that build
 *      `/orgs/${slug}/...` paths (e.g. ThreadsPage navigation) work
 *      unmodified.
 */
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { useState, type ReactNode } from 'react';
import { TooltipProvider } from '@/design-system/primitives/Tooltip';
import { StaticOrgProvider } from '@/lib/orgSlug';
import { MOCK_ORG_SLUG } from '@/mocks';
import { DataContext } from './DataContext';
import { mockAgentsApi } from './_mock-agents';
import { mockAuditApi } from './_mock-audit';
import { mockHealthApi } from './_mock-health';
import { mockKbApi, useMockKbRoutes } from './_mock-kb';
import { mockOrgsApi } from './_mock-orgs';
import { useMockAgentsRoutes, useMockThreadRoutes } from './_mock-routes';
import { mockTalksApi, useMockTalksRoutes } from './_mock-talks';
import { mockTasksApi, useMockTasksRoutes } from './_mock-tasks';
import { mockThreadsApi } from './_mock-threads';

function makePrototypeQueryClient(): QueryClient {
  return new QueryClient({
    defaultOptions: {
      queries: {
        // Mocks are deterministic; no need to refetch.
        staleTime: Infinity,
        refetchOnWindowFocus: false,
        retry: false,
      },
    },
  });
}

export function PrototypeProvider({ children }: { children: ReactNode }): JSX.Element {
  const [client] = useState(makePrototypeQueryClient);
  return (
    <QueryClientProvider client={client}>
      <DataContext.Provider
        value={{
          orgs: mockOrgsApi,
          agents: mockAgentsApi,
          audit: mockAuditApi,
          threads: mockThreadsApi,
          tasks: mockTasksApi,
          kb: mockKbApi,
          talks: mockTalksApi,
          health: mockHealthApi,
          useThreadRoutes: useMockThreadRoutes,
          useTasksRoutes: useMockTasksRoutes,
          useKbRoutes: useMockKbRoutes,
          useTalksRoutes: useMockTalksRoutes,
          useAgentsRoutes: useMockAgentsRoutes,
        }}
      >
        <TooltipProvider delayDuration={300}>
          <StaticOrgProvider slug={MOCK_ORG_SLUG}>{children}</StaticOrgProvider>
        </TooltipProvider>
      </DataContext.Provider>
    </QueryClientProvider>
  );
}
