/**
 * Public, provider-aware health hooks. Features should call these instead of
 * reaching into `@/lib/api/health` directly.
 */
import { useQuery } from '@tanstack/react-query';
import { useData } from '@/design-system/providers/DataContext';
import { health as healthApi } from '@/lib/api';

export const useHealth = () => useData().health.useHealth();

/** Machine-local executor CLI registration status (GET /health/prereqs).
 *  Returns every profile known to the executor registry (built-ins +
 *  custom) with a present/path/hint signal. Used by the New Agent dialog
 *  to derive its selectable-executor list at runtime. */
export function usePrereqs() {
  return useQuery({
    queryKey: ['health', 'prereqs'],
    queryFn: () => healthApi.getPrereqs(),
    staleTime: 30_000,
  });
}
