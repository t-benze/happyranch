/**
 * Read + remove hooks for the machine-global custom adapter store
 * (THR-107 TASK-3792). The routes are daemon-GLOBAL (not org-scoped), so
 * these hooks take no slug. Compositions import from here — never from
 * `@/lib/api/*` directly (the cross-boundary lint forbids it).
 *
 * List/remove are founder-facing MANAGEMENT routes on the standard session
 * bearer, consumed by the Settings ▸ Executors adapter-management view.
 */
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import * as adaptersApi from '@/lib/api/adapters';
import type {
  AdapterEntry,
  RemoveAdapterRequest,
  RemoveAdapterResponse,
} from '@/lib/api/adapters';
import type { QueryLike } from '@/design-system/providers/DataContext';

export type {
  AdapterEntry,
  RemoveAdapterRequest,
  RemoveAdapterResponse,
} from '@/lib/api/adapters';
export type { AdapterEligibility } from '@/lib/api/adapters';

const ADAPTERS_KEY = ['runtime-adapters'] as const;

/** The custom adapters registered in the machine-global durable store,
 *  each with server-authoritative eligibility for recovery actions. */
export function useAdapters(): QueryLike<AdapterEntry[]> {
  return useQuery({
    queryKey: ADAPTERS_KEY,
    queryFn: () => adaptersApi.listAdapters(),
    staleTime: 10_000,
  }) as QueryLike<AdapterEntry[]>;
}

/** Remove an APPROVED adapter by id with an exact snapshot body.
 *  Invalidates the adapters query on success so the list drops the
 *  removed row immediately. */
export function useRemoveAdapter() {
  const qc = useQueryClient();
  return useMutation<RemoveAdapterResponse, unknown, { id: string; body: RemoveAdapterRequest }>({
    mutationFn: ({ id, body }) => adaptersApi.removeAdapter(id, body),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ADAPTERS_KEY });
    },
  });
}

/** The adapters query key, exported so a graceful 404-race handler can
 *  force a refetch even when the remove mutation rejected. */
export { ADAPTERS_KEY };
