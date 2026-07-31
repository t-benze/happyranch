/**
 * Read + remove hooks for the machine-global custom adapter store
 * (THR-107 TASK-3792). The routes are daemon-GLOBAL (not org-scoped), so
 * these hooks take no slug. Compositions import from here — never from
 * `@/lib/api/*` directly (the cross-boundary lint forbids it).
 *
 * List/remove/approve/reject/bind are founder-facing MANAGEMENT routes on
 * the standard session bearer, consumed by the Settings ▸ Executors
 * adapter-management view.
 */
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import * as adaptersApi from '@/lib/api/adapters';
import type {
  AdapterEntry,
  ApproveAdapterRequest,
  ApproveAdapterResponse,
  BindProfileRequest,
  BindProfileResponse,
  RejectAdapterRequest,
  RejectAdapterResponse,
  RemoveAdapterRequest,
  RemoveAdapterResponse,
} from '@/lib/api/adapters';
import type { QueryLike } from '@/design-system/providers/DataContext';

export type {
  AdapterEntry,
  ApproveAdapterRequest,
  ApproveAdapterResponse,
  BindProfileRequest,
  BindProfileResponse,
  RejectAdapterRequest,
  RejectAdapterResponse,
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

/** Approve a PENDING adapter by id with an exact snapshot body (6 material
 *  identity facts). Invalidates the adapters query on success so the list
 *  reflects the APPROVED status immediately. */
export function useApproveAdapter() {
  const qc = useQueryClient();
  return useMutation<ApproveAdapterResponse, unknown, { id: string; body: ApproveAdapterRequest }>({
    mutationFn: ({ id, body }) => adaptersApi.approveAdapter(id, body),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ADAPTERS_KEY });
    },
  });
}

/** Reject/remove a PENDING adapter by id with an exact snapshot body.
 *  Invalidates the adapters query on success so the list drops the
 *  rejected row immediately. No persisted rejected status. */
export function useRejectAdapter() {
  const qc = useQueryClient();
  return useMutation<RejectAdapterResponse, unknown, { id: string; body: RejectAdapterRequest }>({
    mutationFn: ({ id, body }) => adaptersApi.rejectAdapter(id, body),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ADAPTERS_KEY });
    },
  });
}

/** Bind a profile name to an APPROVED adapter. Used by the Settings
 *  pending-approval recovery flow: after approving a PENDING adapter,
 *  the Bind <profile> action calls this to persist the profile binding.
 *  Invalidates the adapters query on success. */
export function useBindAdapterProfile() {
  const qc = useQueryClient();
  return useMutation<BindProfileResponse, unknown, { id: string; body: BindProfileRequest }>({
    mutationFn: ({ id, body }) => adaptersApi.bindAdapterProfile(id, body),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ADAPTERS_KEY });
    },
  });
}

/** The adapters query key, exported so a graceful 404-race handler can
 *  force a refetch even when the remove mutation rejected. */
export { ADAPTERS_KEY };
