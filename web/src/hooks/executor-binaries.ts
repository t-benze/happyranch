/**
 * Read + write hooks for the machine-local executor binary-path registry
 * (THR-085 SLICE A). The routes are daemon-GLOBAL (not org-scoped), so these
 * hooks take no slug. Compositions import from here — never from
 * `@/lib/api/*` directly (the cross-boundary lint forbids it).
 */
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { executorBinaries as api } from '@/lib/api';
import type {
  BinaryRegistryList,
  RegisterBinaryRequest,
  RegisterBinaryResponse,
  ValidateBinaryRequest,
  ValidateBinaryResponse,
} from '@/lib/api/executor-binaries';
import type { QueryLike } from '@/design-system/providers/DataContext';

export {
  EXECUTOR_BINARY_KINDS,
  type ExecutorBinaryKind,
  type BinaryRegistryEntry,
  type BinaryRegistryList,
  type RegisterBinaryRequest,
  type RegisterBinaryResponse,
  type ValidateBinaryRequest,
  type ValidateBinaryResponse,
} from '@/lib/api/executor-binaries';

const REGISTRY_KEY = ['executor-binaries'] as const;

/** The current registry: which kinds have a stored path + whether it's valid. */
export function useExecutorBinaries(): QueryLike<BinaryRegistryList> {
  return useQuery({
    queryKey: REGISTRY_KEY,
    queryFn: () => api.listExecutorBinaries(),
    staleTime: 10_000,
  }) as QueryLike<BinaryRegistryList>;
}

/** Register (or update) an absolute path for a kind. Refetches the registry on
 *  success so the row reflects the new stored path + validity immediately. */
export function useRegisterExecutorBinary() {
  const qc = useQueryClient();
  return useMutation<RegisterBinaryResponse, unknown, RegisterBinaryRequest>({
    mutationFn: (body) => api.registerExecutorBinary(body),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: REGISTRY_KEY });
    },
  });
}

/** Validate a path without storing it — for a pre-commit UI check. */
export function useValidateExecutorBinary() {
  return useMutation<ValidateBinaryResponse, unknown, ValidateBinaryRequest>({
    mutationFn: (body) => api.validateExecutorBinary(body),
  });
}
