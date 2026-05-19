/**
 * Real (daemon-backed) implementation of `HealthApi`.
 *
 * Polls `/health` every 30 s — cheapest query in the system, used as the
 * Dashboard's heartbeat indicator.
 */
import { useQuery } from '@tanstack/react-query';
import { health as healthApi } from '@/lib/api';
import type { HealthResponse } from '@/lib/api/types';
import type { HealthApi, QueryLike } from './DataContext';

function useHealth(): QueryLike<HealthResponse> {
  return useQuery({
    queryKey: ['health'],
    queryFn: () => healthApi.getHealth(),
    refetchInterval: 30_000,
  });
}

export const realHealthApi: HealthApi = {
  useHealth,
};
