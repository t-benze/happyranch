/**
 * Real (daemon-backed) implementation of `HealthApi`.
 *
 * `/health` is the one startup observation (THR-308). `useHealth` is its sole
 * polling owner, mounted once by the app shell: every 30 s once positively
 * ready, every 2 s while startup is unavailable, so readiness is observed
 * promptly without a poller per control. `useStartup` subscribes to the same
 * cached query without polling and derives the admission view from it.
 *
 * Freshness also expires with no network or render event: one expiry owner per
 * QueryClient (shared by every mounted subscriber) arms at most one timer at
 * the current ready observation's 90 s deadline and notifies subscribers, so
 * mounted routes, streams and the banner re-derive a held view even while the
 * poller's next fetch is pending or paused. It never fetches or writes cache.
 */
import { hashKey, useQuery, useQueryClient, type QueryClient } from '@tanstack/react-query';
import { useCallback, useMemo, useSyncExternalStore } from 'react';
import { health as healthApi } from '@/lib/api';
import type { HealthResponse } from '@/lib/api/types';
import {
  HEALTH_QUERY_KEY,
  STARTUP_POLL_HELD_MS,
  STARTUP_MAX_AGE_MS,
  STARTUP_POLL_READY_MS,
  assertStartupAdmitted,
  deriveStartupView,
  isStartupAdmittedIn,
  readStartupView,
  type StartupAdmission,
} from '@/lib/startup';
import type { HealthApi, QueryLike } from './DataContext';

function useHealth(): QueryLike<HealthResponse> {
  return useQuery({
    queryKey: HEALTH_QUERY_KEY,
    queryFn: () => healthApi.getHealth(),
    refetchInterval: (query) =>
      deriveStartupView(query.state).admitted ? STARTUP_POLL_READY_MS : STARTUP_POLL_HELD_MS,
  });
}

const HEALTH_QUERY_HASH = hashKey(HEALTH_QUERY_KEY);

interface ExpiryOwner {
  listeners: Set<() => void>;
  /** Bumped when a ready observation expires; the snapshot subscribers read. */
  expirations: number;
  timer: ReturnType<typeof setTimeout> | null;
  unsubscribeCache: (() => void) | null;
}

const expiryOwners = new WeakMap<QueryClient, ExpiryOwner>();

/** (Re)arm the single timer for the current observation's freshness deadline. */
function armExpiry(qc: QueryClient, owner: ExpiryOwner): void {
  if (owner.timer !== null) clearTimeout(owner.timer);
  owner.timer = null;
  const state = qc.getQueryState(HEALTH_QUERY_KEY);
  if (!state || !readStartupView(qc).admitted) return;
  const delay = Math.max(0, state.dataUpdatedAt + STARTUP_MAX_AGE_MS + 1 - Date.now());
  owner.timer = setTimeout(() => {
    owner.timer = null;
    // A timer that fires before the deadline re-arms; an expired view notifies.
    if (readStartupView(qc).admitted) {
      armExpiry(qc, owner);
      return;
    }
    owner.expirations += 1;
    owner.listeners.forEach((listener) => listener());
  }, delay);
}

function subscribeExpiry(qc: QueryClient, listener: () => void): () => void {
  let owner = expiryOwners.get(qc);
  if (!owner) {
    owner = { listeners: new Set(), expirations: 0, timer: null, unsubscribeCache: null };
    expiryOwners.set(qc, owner);
  }
  const current = owner;
  current.listeners.add(listener);
  if (current.unsubscribeCache === null) {
    current.unsubscribeCache = qc.getQueryCache().subscribe((event) => {
      if (event.query.queryHash !== HEALTH_QUERY_HASH) return;
      if (event.type === 'added' || event.type === 'removed' || event.type === 'updated') armExpiry(qc, current);
    });
    armExpiry(qc, current);
  }
  return () => {
    current.listeners.delete(listener);
    if (current.listeners.size > 0) return;
    current.unsubscribeCache?.();
    current.unsubscribeCache = null;
    if (current.timer !== null) clearTimeout(current.timer);
    current.timer = null;
  };
}

/** Non-polling reader of the shared observation; never fetches on its own. */
export function useRealStartup(): StartupAdmission {
  const qc = useQueryClient();
  const q = useQuery({
    queryKey: HEALTH_QUERY_KEY,
    queryFn: () => healthApi.getHealth(),
    enabled: false,
  });
  // Re-render (and re-derive against the clock) when the shared deadline passes.
  const subscribe = useCallback((listener: () => void) => subscribeExpiry(qc, listener), [qc]);
  useSyncExternalStore(subscribe, () => expiryOwners.get(qc)?.expirations ?? 0);
  const { phase, admitted, errorCode } = deriveStartupView({
    status: q.status, data: q.data, dataUpdatedAt: q.dataUpdatedAt,
  });
  const isAdmittedNow = useCallback(() => isStartupAdmittedIn(qc), [qc]);
  const assertAdmittedNow = useCallback(() => assertStartupAdmitted(qc), [qc]);
  return useMemo(
    () => ({ phase, admitted, errorCode, isAdmittedNow, assertAdmittedNow }),
    [phase, admitted, errorCode, isAdmittedNow, assertAdmittedNow],
  );
}

export const realHealthApi: HealthApi = {
  useHealth,
  useStartup: useRealStartup,
};
