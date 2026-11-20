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
 *         the last observer leaves (unmount, or the owner gives the same
 *         observer a different logical key: R2-2); a new mount or new key
 *         reads the new target;
 *       * every other mounted observer moves to its new-generation key (one
 *         fresh fetch under the new generation);
 *       * every unobserved or moved old-generation query is removed.
 *   - transient recovery (F3): when busy/throttled clears (native `ready` or a
 *     later forwarded success), failed reads whose cause was transient native
 *     load are refetched once, including a read that was still pending when
 *     the signal fired and failed afterwards (R2-1, recovery epoch). Mutations
 *     are never retried.
 * Feature query keys are unchanged, so partial-key invalidation keeps working.
 */
import { hashKey, partialMatchKey, type Query, type QueryClient, type QueryKey, type QueryObserver } from '@tanstack/react-query';
import { isTransientNativeFailure, StaleTargetError } from '../api/client';
import { subscribeTransientRecovery, transientRecoveryEpoch } from './notices';
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

/**
 * Keep one observer on its current (older-target) query, without fetching,
 * for as long as its owner keeps asking for the same logical query key.
 * When the owner re-keys the same observer (R2-2: e.g. Settings following an
 * org change), the pin is released (`onUnpin`) and the new key resolves and
 * fetches under the current generation.
 */
function pinObserver(observer: AnyObserver, queryHash: string, onUnpin: () => void): void {
  const logicalKey = hashKey(observer.options.queryKey);
  const sameKey = (options: AnyOptions): boolean => hashKey(options.queryKey) === logicalKey;
  const pin = (options: AnyOptions): AnyOptions => ({
    ...options,
    queryHash,
    enabled: false,
    queryFn: suspendedFetch,
  });
  const fresh = (options: AnyOptions): AnyOptions => {
    // Re-resolve under the current generation's key.
    const { queryHash: _staleHash, ...rest } = options;
    return rest as AnyOptions;
  };
  const setOptions = observer.setOptions.bind(observer);
  const getOptimisticResult = observer.getOptimisticResult.bind(observer);
  const unpin = (): void => {
    observer.setOptions = setOptions;
    observer.getOptimisticResult = getOptimisticResult;
    onUnpin();
  };
  // React's useQuery re-supplies options (and a new-generation hash) on every
  // render through these two calls; the pin keeps the original entry while the
  // logical key is unchanged.
  observer.setOptions = (options: AnyOptions) => {
    if (sameKey(options)) return setOptions(pin(options));
    unpin();
    return setOptions(fresh(options));
  };
  observer.getOptimisticResult = ((options: AnyOptions) =>
    getOptimisticResult(
      (sameKey(options) ? pin(options) : fresh(options)) as Parameters<AnyObserver['getOptimisticResult']>[0],
    )) as AnyObserver['getOptimisticResult'];
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

  // R2-1: the recovery epoch each query's current fetch started under.
  const fetchEpochs = new WeakMap<Query, number>();
  const offCache = cache.subscribe((event) => {
    if (event.type === 'updated') {
      if (event.action.type === 'fetch') fetchEpochs.set(event.query, transientRecoveryEpoch());
      else if (event.action.type === 'error') reconcileRecovery(event.query);
    }
    if (!holds.has(event.query)) return;
    if (event.type === 'removed') release(event.query);
    else if (event.type === 'observerRemoved' && event.query.getObserversCount() === 0) {
      // The owner re-keyed or unmounted: drop the older-target entry.
      cache.remove(event.query);
    }
  });

  /**
   * A transient failure that settles after a recovery signal fired while it
   * was pending (bootstrap busy body, final backoff attempt) missed that
   * signal: refetch it once, outside the cache notification. Queries only:
   * mutations are never replayed.
   */
  function reconcileRecovery(query: Query): void {
    const started = fetchEpochs.get(query);
    if (started === undefined || started === transientRecoveryEpoch()) return;
    if (!isTransientNativeFailure(query.state.error)) return;
    fetchEpochs.delete(query);
    setTimeout(() => {
      if (cache.get(query.queryHash) !== query || !query.isActive()) return;
      if (query.state.status !== 'error' || query.state.fetchStatus !== 'idle') return;
      void client.refetchQueries({ predicate: (candidate) => candidate === query }, { cancelRefetch: false });
    }, 0);
  }

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
          // Leaving the held query drops it once no observer remains
          // (observerRemoved below), which releases the hold.
          pinObserver(observer, query.queryHash, () => pinned.delete(observer));
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
