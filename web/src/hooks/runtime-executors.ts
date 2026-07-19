/**
 * Read + remove hooks for the machine-global custom executor profile store
 * (THR-107 S4). The routes are daemon-GLOBAL (not org-scoped), so these hooks
 * take no slug. Compositions import from here — never from `@/lib/api/*`
 * directly (the cross-boundary lint forbids it).
 *
 * List/remove are the founder-facing MANAGEMENT routes on the standard session
 * bearer (same posture as the executor-binary registry), consumed by the
 * Settings ▸ Executors custom-profiles view.
 */
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { runtimeExecutors as api } from '@/lib/api';
import type {
  RemoveRuntimeProfileResponse,
  RuntimeProfileList,
} from '@/lib/api/runtime-executors';
import type { QueryLike } from '@/design-system/providers/DataContext';

export type {
  RuntimeProfileEntry,
  RuntimeProfileList,
  RemoveRuntimeProfileResponse,
} from '@/lib/api/runtime-executors';

const RUNTIME_PROFILES_KEY = ['runtime-profiles'] as const;

/** The custom executor profiles registered in the machine-global runtime
 *  store, each with its executable, adapter, and present/path health signal. */
export function useRuntimeProfiles(): QueryLike<RuntimeProfileList> {
  return useQuery({
    queryKey: RUNTIME_PROFILES_KEY,
    queryFn: () => api.listRuntimeProfiles(),
    staleTime: 10_000,
  }) as QueryLike<RuntimeProfileList>;
}

/** Remove a custom profile by name. Invalidates the profiles query on success
 *  so the list drops the removed row immediately — the same cache-invalidation
 *  pattern the registered-binary registry uses (THR-107 S3). */
export function useRemoveRuntimeProfile() {
  const qc = useQueryClient();
  return useMutation<RemoveRuntimeProfileResponse, unknown, string>({
    mutationFn: (name) => api.removeRuntimeProfile(name),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: RUNTIME_PROFILES_KEY });
    },
  });
}

/** The profiles query key, exported so a graceful 404-race handler can force a
 *  refetch even when the remove mutation rejected (nothing to invalidate on). */
export { RUNTIME_PROFILES_KEY };
