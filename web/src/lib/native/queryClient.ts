/**
 * TanStack Query integration for the native target generation (S5-W1).
 *
 * Desktop only (browser mode returns without touching the client):
 *   - query hashes include the generation of the last switch, so a cache
 *     entry from one target can never satisfy a read for another;
 *   - on a generation change: cancel in-flight queries, then
 *       * editor custody (F2): a mounted observer of a custody key (the
 *         caller's prefixes, e.g. Settings) that already shows data stays on
 *         that older-target entry, suspended: no fetch, refetch or
 *         invalidation refetch, and no new-target data ever lands in it. Its
 *         data object is unchanged, so data-gated editors stay mounted and
 *         keep their drafts. While any such hold exists no change is sent to
 *         the current target (Not sent, lib/api/client.ts). The hold ends when
 *         the last observer leaves (the owner re-keys or unmounts); a new
 *         mount reads the new target under its own key;
 *       * every other mounted observer moves to its new-generation key (one
 *         fresh fetch under the new generation);
 *       * every unobserved or moved old-generation query is removed.
 *   - transient recovery (F3): when busy/throttled clears (native `ready` or a
 *     later forwarded success), failed reads whose cause was transient native
 *     load are refetched once. Mutations are never retried.
 * Feature query keys are unchanged, so partial-key invalidation keeps working.
 */
import { hashKey, partialMatchKey, type Query, type QueryClient, type QueryKey, type QueryObserver } from '@tanstack/react-query';
import { isTransientNativeFailure, StaleTargetError } from '../api/client';
import { subscribeTransientRecovery } from './notices';
import {
  ensureNativeTarget,
  holdTargetCustody,
  isNativeTarget,
  onTargetGenerationChange,
  queryKeyGeneration,
} from './target';

export function nativeQueryKeyHash(queryKey: QueryKey): string {
  return hashKey([{ target: queryKeyGeneration() }, ...queryKey]);
}

export interface NativeQueryClientOptions {
  /** Query-key prefixes whose mounted, loaded observers keep editor custody. */
  custody?: readonly QueryKey[];
}

type AnyObserver = QueryObserver<unknown, Error, unknown, unknown, QueryKey>;
type AnyOptions = AnyObserver['options'];

const suspendedFetch = (): Promise<never> => Promise.reject(new StaleTargetError());

/** Keep one observer on its current (older-target) query, without fetching. */
function pinObserver(observer: AnyObserver, queryHash: string): void {
  const pin = (options: AnyOptions): AnyOptions => ({
    ...options,
    queryHash,
    enabled: false,
    queryFn: suspendedFetch,
  });
  const setOptions = observer.setOptions.bind(observer);
  const getOptimisticResult = observer.getOptimisticResult.bind(observer);
  // React's useQuery re-supplies options (and a new-generation hash) on every
  // render through these two calls; the pin keeps the original entry.
  observer.setOptions = (options: AnyOptions) => setOptions(pin(options));
  observer.getOptimisticResult = ((options: AnyOptions) =>
    getOptimisticResult(pin(options) as Parameters<AnyObserver['getOptimisticResult']>[0])) as AnyObserver['getOptimisticResult'];
  observer.setOptions(observer.options);
}

/** Wire a QueryClient to the generation store; returns a detach function. */
export function attachNativeQueryClient(client: QueryClient, opts: NativeQueryClientOptions = {}): () => void {
  if (!isNativeTarget()) return () => {};
  ensureNativeTarget();
  const custody = opts.custody ?? [];
  const defaults = client.getDefaultOptions();
  client.setDefaultOptions({
    ...defaults,
    queries: { ...defaults.queries, queryKeyHashFn: nativeQueryKeyHash },
  });
  const cache = client.getQueryCache();
  const pinned = new WeakSet<AnyObserver>();
  const holds = new Map<Query, () => void>();
  const isCustody = (query: Query): boolean => custody.some((prefix) => partialMatchKey(query.queryKey, prefix));

  const release = (query: Query): void => {
    holds.get(query)?.();
    holds.delete(query);
  };

  const offCache = cache.subscribe((event) => {
    if (!holds.has(event.query)) return;
    if (event.type === 'removed') release(event.query);
    else if (event.type === 'observerRemoved' && event.query.getObserversCount() === 0) {
      // The owner re-keyed or unmounted: drop the older-target entry.
      cache.remove(event.query);
    }
  });

  const offChange = onTargetGenerationChange(() => {
    void client.cancelQueries();
    for (const query of cache.getAll()) {
      const observers = [...query.observers] as AnyObserver[];
      const keep = observers.length > 0 && query.state.data !== undefined && (holds.has(query) || isCustody(query));
      if (keep) {
        if (!holds.has(query)) holds.set(query, holdTargetCustody());
        for (const observer of observers) {
          if (pinned.has(observer)) continue;
          pinned.add(observer);
          pinObserver(observer, query.queryHash);
        }
        continue;
      }
      for (const observer of observers) {
        if (pinned.has(observer)) continue;
        // Drop the stored hash so the observer re-resolves under the new
        // generation's key (and fetches there if mounted and enabled).
        const { queryHash: _oldHash, ...options } = observer.options;
        observer.setOptions(options as typeof observer.options);
      }
      cache.remove(query);
    }
  });

  const offRecovery = subscribeTransientRecovery(() => {
    void client.refetchQueries(
      {
        type: 'active',
        predicate: (query) =>
          query.state.status === 'error' &&
          query.state.fetchStatus === 'idle' &&
          isTransientNativeFailure(query.state.error),
      },
      { cancelRefetch: false },
    );
  });

  return () => {
    offChange();
    offRecovery();
    offCache();
    for (const query of [...holds.keys()]) release(query);
  };
}
