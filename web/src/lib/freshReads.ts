/**
 * Reads newly issued AFTER a call starts (THR-402 A9 recovery evidence).
 *
 * `refetchQueries` alone can join a fetch that is already in flight (TanStack
 * Query reuses a running first load that has no data yet), and
 * `dataUpdatedAt` is a completion time, so neither proves a read was issued
 * after a mutation attempt. This helper first retires (cancels) every
 * in-flight fetch for each exact key, then issues a refetch and accepts only a
 * successful data update counted after that retirement. Any key that is
 * missing, disabled, failed, still fetching or not updated gives `null`
 * (no trustworthy observation): callers keep their result unknown.
 */
import type { QueryClient, QueryKey } from '@tanstack/react-query';

export async function freshReads(qc: QueryClient, keys: readonly QueryKey[]): Promise<unknown[] | null> {
  await Promise.all(keys.map((queryKey) => qc.cancelQueries({ queryKey, exact: true })));
  // Synchronously after retirement: every later data update comes from a
  // fetch issued after this point.
  const before = keys.map((queryKey) => qc.getQueryState(queryKey)?.dataUpdateCount ?? null);
  if (before.some((count) => count === null)) return null;
  await Promise.all(keys.map((queryKey) => qc.refetchQueries({ queryKey, exact: true })));
  const results: unknown[] = [];
  for (const [index, queryKey] of keys.entries()) {
    const state = qc.getQueryState(queryKey);
    if (
      !state ||
      state.status !== 'success' ||
      state.fetchStatus !== 'idle' ||
      state.data === undefined ||
      state.dataUpdateCount <= (before[index] as number)
    ) {
      return null;
    }
    results.push(state.data);
  }
  return results;
}

/**
 * A mutation's readback: retire every in-flight read under each prefix, then
 * invalidate (refetch active) and settle them all. Without the retirement an
 * invalidation joins a running first load that may carry a pre-mutation
 * snapshot.
 */
export async function refreshAfterMutation(qc: QueryClient, prefixes: readonly QueryKey[]): Promise<void> {
  await Promise.all(prefixes.map((queryKey) => qc.cancelQueries({ queryKey })));
  await Promise.all(prefixes.map((queryKey) => qc.invalidateQueries({ queryKey })));
}
