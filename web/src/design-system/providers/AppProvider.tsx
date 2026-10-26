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
import { MutationCache, QueryCache, QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { useState, type ReactNode } from 'react';
import { TooltipProvider } from '@/design-system/primitives/Tooltip';
import { HEALTH_QUERY_KEY, isStartupUnavailableError, StartupHeldError } from '@/lib/startup';
import { DataContext } from './DataContext';
import { realIdentitiesApi } from './_real-identities';

import { realWorkflowsApi } from './_real-workflows';
import { realAgentsApi } from './_real-agents';
import { realAuthorityPolicyApi } from './_real-authority-policy';
import { realAuditApi } from './_real-audit';
import { realDashboardApi } from './_real-dashboard';
import { realSettingsApi } from './_real-settings';
import { realHealthApi } from './_real-health';
import { realKbApi } from './_real-kb';
import { realDreamsApi } from './_real-dreams';
import { realSkillsApi } from './_real-skills';
import { realOrgsApi } from './_real-orgs';
import {
  useRealAgentsRoutes,
  useRealDreamsRoutes,
  useRealJobsRoutes,
  useRealKbRoutes,
  useRealTasksRoutes,
  useRealThreadRoutes,
} from './_real-routes';
import { realJobsApi } from './_real-jobs';
import { realTasksApi } from './_real-tasks';
import { realTeamsApi } from './_real-teams';
import { realThreadsApi } from './_real-threads';
import { realWorkHoursApi } from './_real-work-hours';

export function makeQueryClient(): QueryClient {
  // A daemon startup refusal (503 daemon_recovering / daemon_recovery_failed /
  // startup_*) means the request was NOT accepted. Refresh the one shared
  // startup observation so every surface converges on the actual state. The
  // refused operation itself is never retried or replayed.
  const observeStartup = (error: unknown) => {
    if (isStartupUnavailableError(error) && !(error instanceof StartupHeldError)) {
      void client.invalidateQueries({ queryKey: HEALTH_QUERY_KEY, exact: true });
    }
  };
  const client: QueryClient = new QueryClient({
    queryCache: new QueryCache({ onError: observeStartup }),
    mutationCache: new MutationCache({ onError: observeStartup }),
    defaultOptions: {
      queries: {
        staleTime: 30_000,
        refetchOnWindowFocus: false,
        retry: false,
      },
    },
  });
  return client;
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
          workflows: realWorkflowsApi,
          orgs: realOrgsApi,
          agents: realAgentsApi,
          identities: realIdentitiesApi,
          authorityPolicy: realAuthorityPolicyApi,
          audit: realAuditApi,
          threads: realThreadsApi,
          tasks: realTasksApi,
          dashboard: realDashboardApi,
          settings: realSettingsApi,
          kb: realKbApi,
          dreams: realDreamsApi,
          skills: realSkillsApi,
          teams: realTeamsApi,
          health: realHealthApi,
          jobs: realJobsApi,
          workHours: realWorkHoursApi,
          useThreadRoutes: useRealThreadRoutes,
          useTasksRoutes: useRealTasksRoutes,
          useKbRoutes: useRealKbRoutes,
          useAgentsRoutes: useRealAgentsRoutes,
          useJobsRoutes: useRealJobsRoutes,
          useDreamsRoutes: useRealDreamsRoutes,
        }}
      >
        <TooltipProvider delayDuration={300}>{children}</TooltipProvider>
      </DataContext.Provider>
    </QueryClientProvider>
  );
}
