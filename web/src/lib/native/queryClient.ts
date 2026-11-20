/**
 * TanStack Query integration for the native target generation (S5-W1).
 *
 * Desktop only (browser mode returns without touching the client):
 *   - query hashes include the generation of the last switch, so a cache
 *     entry from one target can never satisfy a read for another;
 *   - on a generation change: cancel in-flight queries, move every mounted
 *     observer to its new-generation key (one fresh fetch under the new
 *     generation) and remove all old-generation queries.
 * Component state (open drafts, focus, selection) is untouched: nothing is
 * remounted. Feature query keys are unchanged, so partial-key invalidation
 * keeps working.
 */
import { hashKey, type QueryClient, type QueryKey } from '@tanstack/react-query';
import { ensureNativeTarget, isNativeTarget, onTargetGenerationChange, queryKeyGeneration } from './target';

export function nativeQueryKeyHash(queryKey: QueryKey): string {
  return hashKey([{ target: queryKeyGeneration() }, ...queryKey]);
}

/** Wire a QueryClient to the generation store; returns a detach function. */
export function attachNativeQueryClient(client: QueryClient): () => void {
  if (!isNativeTarget()) return () => {};
  ensureNativeTarget();
  const defaults = client.getDefaultOptions();
  client.setDefaultOptions({
    ...defaults,
    queries: { ...defaults.queries, queryKeyHashFn: nativeQueryKeyHash },
  });
  return onTargetGenerationChange(() => {
    void client.cancelQueries();
    const cache = client.getQueryCache();
    const previous = cache.getAll();
    for (const query of previous) {
      for (const observer of [...query.observers]) {
        // Drop the stored hash so the observer re-resolves under the new
        // generation's key (and fetches there if mounted and enabled).
        const { queryHash: _oldHash, ...options } = observer.options;
        observer.setOptions(options as typeof observer.options);
      }
    }
    for (const query of previous) cache.remove(query);
  });
}
