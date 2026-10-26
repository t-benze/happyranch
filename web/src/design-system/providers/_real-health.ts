/**
 * Real (daemon-backed) implementation of `HealthApi`.
 *
 * `/health` is the one startup observation (THR-308). `useHealth` is its sole
 * polling owner, mounted once by the app shell: every 30 s once positively
 * ready, every 2 s while startup is unavailable, so readiness is observed
 * promptly without a poller per control. `useStartup` subscribes to the same
 * cached query without polling and derives the admission view from it.
 */
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { useCallback, useMemo } from 'react';
import { health as healthApi } from '@/lib/api';
import type { HealthResponse } from '@/lib/api/types';
import {
  HEALTH_QUERY_KEY,
  STARTUP_POLL_HELD_MS,
  STARTUP_POLL_READY_MS,
  assertStartupAdmitted,
  deriveStartupView,
  isStartupAdmittedIn,
  type StartupAdmission,
} from '@/lib/startup';
import type { HealthApi, QueryLike } from './DataContext';

function useHealth(): QueryLike<HealthResponse> {
  return useQuery({
    queryKey: HEALTH_QUERY_KEY,
    queryFn: () => healthApi.getHealth(),
    refetchInterval: (query) =>
      deriveStartupView(query.state).admitted ? STARTUP_POLL_READY_MS : STARTUP_POLL_HELD_MS,
  });
}

/** Non-polling reader of the shared observation; never fetches on its own. */
export function useRealStartup(): StartupAdmission {
  const qc = useQueryClient();
  const q = useQuery({
    queryKey: HEALTH_QUERY_KEY,
    queryFn: () => healthApi.getHealth(),
    enabled: false,
  });
  const { phase, admitted, errorCode } = deriveStartupView({
    status: q.status, data: q.data, dataUpdatedAt: q.dataUpdatedAt,
  });
  const isAdmittedNow = useCallback(() => isStartupAdmittedIn(qc), [qc]);
  const assertAdmittedNow = useCallback(() => assertStartupAdmitted(qc), [qc]);
  return useMemo(
    () => ({ phase, admitted, errorCode, isAdmittedNow, assertAdmittedNow }),
    [phase, admitted, errorCode, isAdmittedNow, assertAdmittedNow],
  );
}

export const realHealthApi: HealthApi = {
  useHealth,
  useStartup: useRealStartup,
};
