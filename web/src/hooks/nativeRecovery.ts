import { useCallback, useSyncExternalStore } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import {
  getNativeRecoveryState,
  retryNativeRecovery,
  subscribeNativeRecovery,
  type NativeRecoveryState,
} from '@/lib/nativeRecovery';

/** The app-wide native recovery state (THR-387 S1b), or null. */
export function useNativeRecovery(): NativeRecoveryState | null {
  return useSyncExternalStore(subscribeNativeRecovery, getNativeRecoveryState, getNativeRecoveryState);
}

/**
 * Explicit Retry/Recheck: clear the state, retry latched read owners that
 * failed on a recovery answer (Threads committed pages) and refetch active
 * queries. A request that still meets a native recovery answer reports the
 * state again. Drafts and mounted page state are untouched; nothing is sent
 * except those reads.
 */
export function useRetryNativeRecovery(): () => void {
  const queryClient = useQueryClient();
  return useCallback(() => {
    retryNativeRecovery();
    void queryClient.refetchQueries({ type: 'active' });
  }, [queryClient]);
}
