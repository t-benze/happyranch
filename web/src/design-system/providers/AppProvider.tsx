/**
 * Production data provider.
 *
 * Owns:
 *   1. The TanStack `QueryClient` (moved from `App.tsx`).
 *   2. The `DataContext` value pointing every domain hook at its real,
 *      daemon-backed implementation.
 *
 * Mounts at the App root (above `<AppRoutes />`). The active org slug is
 * still resolved per-route by `<OrgProvider>` inside `routes.tsx` — it
 * writes to `OrgSlugContext`, which `useOrgSlug` / `useOrgSlugOptional`
 * read.
 *
 * Auth bootstrap (`@/lib/auth`) is intentionally NOT initiated here. The
 * existing model is lazy: `lib/api/client.ts` calls `getToken()` on the first
 * request and stashes the result in `sessionStorage`. AppProvider preserves
 * that behaviour — wrapping the SPA in this provider must not change network
 * timing.
 */
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { useState, type ReactNode } from 'react';
import { TooltipProvider } from '@/design-system/primitives/Tooltip';
import { DataContext } from './DataContext';
import { realAgentsApi } from './_real-agents';
import { realOrgsApi } from './_real-orgs';
import {
  useRealAgentsRoutes,
  useRealTalksRoutes,
  useRealTasksRoutes,
  useRealThreadRoutes,
} from './_real-routes';
import { realTalksApi } from './_real-talks';
import { realTasksApi } from './_real-tasks';
import { realThreadsApi } from './_real-threads';

export function makeQueryClient(): QueryClient {
  return new QueryClient({
    defaultOptions: {
      queries: {
        staleTime: 30_000,
        refetchOnWindowFocus: false,
        retry: false,
      },
    },
  });
}

interface AppProviderProps {
  children: ReactNode;
  /** Test-only: inject a pre-seeded QueryClient. */
  client?: QueryClient;
}

export function AppProvider({ children, client }: AppProviderProps): JSX.Element {
  const [defaultClient] = useState(makeQueryClient);
  const qc = client ?? defaultClient;
  return (
    <QueryClientProvider client={qc}>
      <DataContext.Provider
        value={{
          orgs: realOrgsApi,
          agents: realAgentsApi,
          threads: realThreadsApi,
          tasks: realTasksApi,
          talks: realTalksApi,
          useThreadRoutes: useRealThreadRoutes,
          useTasksRoutes: useRealTasksRoutes,
          useTalksRoutes: useRealTalksRoutes,
          useAgentsRoutes: useRealAgentsRoutes,
        }}
      >
        <TooltipProvider delayDuration={300}>{children}</TooltipProvider>
      </DataContext.Provider>
    </QueryClientProvider>
  );
}
