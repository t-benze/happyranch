/**
 * Real (daemon-backed) implementation of `SettingsApi`.
 *
 * Phase 1: read-only `useSettings` hook backed by GET /settings.
 * Phase 2: editable `useUpdateOrgSettings` mutation backed by PUT /settings/org.
 * Coding: org-keyed GET/PUT /settings/coding view plus explicit save/readback.
 */
import { useMemo, useSyncExternalStore } from 'react';
import { useMutation, useQuery, useQueryClient, type QueryClient } from '@tanstack/react-query';
import { useParams } from 'react-router-dom';
import { settings as settingsApi } from '@/lib/api';
import type {
  CodingSettingsCommand,
  CodingSettingsView,
  DaemonCapacitySnapshot,
  NextWakesResponse,
  OrgSettingsPatch,
  SettingsSnapshot,
  DaemonCapacityWrite,
} from '@/lib/api/types';
import type { SettingsApi, QueryLike, CodingSendFence, CodingSettingsActions, CodingSettingsQueryLike } from './DataContext';
import {
  acceptCapacityWrite,
  capacityObservation,
  capacityQueryKey,
  capacityWriteSettlement,
  isUsableCapacitySnapshot,
  isDroppedCapacityRead,
  nextCapacitySeq,
  publishCapacityRead,
  publishCapacityReadFailure,
  recordCapacityWriteSettlement,
  recordUnusableCapacityWrite,
  type CapacityMutationLike,
  type CapacityQueryLike,
} from './_capacity-ordering';

function useRealOrgSlug(): string {
  const { slug } = useParams<{ slug: string }>();
  return slug ?? '';
}

function useSettings(): QueryLike<SettingsSnapshot> {
  const slug = useRealOrgSlug();
  return useQuery({
    queryKey: ['settings', slug],
    queryFn: () => settingsApi.getSettings(slug),
    enabled: !!slug,
    staleTime: 30_000,
  }) as QueryLike<SettingsSnapshot>;
}

function useUpdateOrgSettings() {
  const slug = useRealOrgSlug();
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (patch: OrgSettingsPatch) =>
      settingsApi.putOrgSettings(slug, patch),
    onSuccess: (data: SettingsSnapshot) => {
      qc.setQueryData(['settings', slug], data);
    },
  });
}

/**
 * Capacity read. The query key is scoped to the org slug so navigating between
 * orgs cannot serve one org's cached snapshot — and therefore one org's
 * `revision` — to another (TASK-8537 G2).
 *
 * Ordering and receipt metadata is produced HERE, before the cache write, not
 * in component state: component state cannot distinguish a genuine network
 * response from a re-render, a cache reread or a remount, and React Query's
 * structural sharing returns a reference-identical object for an identical
 * successful refetch.
 */
function useDaemonCapacity(): CapacityQueryLike<DaemonCapacitySnapshot> {
  const slug = useRealOrgSlug();
  const qc = useQueryClient();
  const key = capacityQueryKey(slug);
  const query = useQuery({
    queryKey: key,
    queryFn: async (): Promise<DaemonCapacitySnapshot> => {
      const issuedSeq = nextCapacitySeq();
      // The accepted snapshot MUST be read at SETTLEMENT, never at issue: a
      // read issued before the write settles would otherwise capture the
      // PRE-SAVE cache value and republish it over the accepted result — the
      // exact revert this rule exists to prevent.
      const acceptedNow = () => qc.getQueryData<DaemonCapacitySnapshot>(key);
      try {
        const data = await settingsApi.getDaemonCapacity(slug);
        const settledSeq = nextCapacitySeq();
        if (isDroppedCapacityRead(slug, issuedSeq)) {
          // S5-R1/R2/R8: never publishes, never moves base, never advances the
          // receipt. Returning the already-accepted snapshot keeps the cache
          // and the rendered surface exactly as the accepted result left them.
          const accepted = acceptedNow();
          if (accepted !== undefined) return accepted;
        } else {
          publishCapacityRead(slug, issuedSeq, settledSeq, data, Date.now());
        }
        return data;
      } catch (error) {
        const settledSeq = nextCapacitySeq();
        const accepted = acceptedNow();
        if (isDroppedCapacityRead(slug, issuedSeq) && accepted !== undefined) {
          // S5-R3/R8: an obsolete failure grants no ordering privilege and must
          // not downgrade a state a newer usable result already established.
          return accepted;
        }
        publishCapacityReadFailure(slug, issuedSeq, settledSeq);
        throw error;
      }
    },
    enabled: !!slug,
  });
  return {
    data: query.data,
    isLoading: query.isLoading,
    isError: query.isError,
    error: query.error,
    refetch: () => query.refetch(),
    isFetching: query.isFetching,
    observation: capacityObservation(slug),
  };
}

function useUpdateDaemonCapacity(): CapacityMutationLike<DaemonCapacityWrite, DaemonCapacitySnapshot> {
  const slug = useRealOrgSlug();
  const qc = useQueryClient();
  const key = capacityQueryKey(slug);
  const mutation = useMutation({
    mutationFn: async (body: DaemonCapacityWrite) => {
      await qc.cancelQueries({ queryKey: key });
      const data = await settingsApi.putDaemonCapacity(slug, body);
      const settledSeq = nextCapacitySeq();
      // Keyed on the write's SETTLEMENT seq: a read issued during the write
      // carries a higher ISSUE seq and would survive an issue-keyed filter.
      // A write whose body is not snapshot-shaped still settles and still
      // fences later reads, but it never becomes an accepted observation and
      // never reaches the cache — the caller classifies it as an unknown
      // outcome instead.
      // C3: record which settlement THIS request produced before anything can
      // render it, so the issuing editor recognises its own settlement exactly.
      const usable = isUsableCapacitySnapshot(data);
      recordCapacityWriteSettlement(body, {
        settledSeq,
        outcome: usable ? 'usable' : 'unusable',
      });
      if (usable) {
        acceptCapacityWrite(slug, settledSeq, data, Date.now());
      } else {
        // R7: acceptance uses the SAME capacity-local semantic classifier the
        // view applies. A body whose numerics did not survive JSON.parse as
        // safe integers — or that violates the domain/relational matrix — is
        // not a usable result, so it never becomes an accepted observation and
        // never advances the receipt. It still fences later reads.
        recordUnusableCapacityWrite(slug, settledSeq);
      }
      return data;
    },
    onSuccess: (data) => {
      // Only a usable snapshot may enter the capacity cache (accepted 15.5).
      if (isUsableCapacitySnapshot(data)) qc.setQueryData(key, data);
    },
  });
  return {
    mutateAsync: mutation.mutateAsync,
    isPending: mutation.isPending,
    settlementOf: capacityWriteSettlement,
  };
}

function useNextWakes(
  agent: string | undefined,
  count = 5,
): QueryLike<NextWakesResponse> {
  const slug = useRealOrgSlug();
  return useQuery({
    queryKey: ['work-hours-next-wakes', slug, agent, count],
    queryFn: () => settingsApi.getNextWakes(slug, agent as string, count),
    enabled: !!slug && !!agent,
    staleTime: 30_000,
  }) as QueryLike<NextWakesResponse>;
}

// ── Founder coding settings ──
//
// One org-keyed view per slug. Ordering is decided at SETTLEMENT: a read
// issued before the latest accepted write/readback for the same org settled
// can never replace that newer accepted view (a reply can arrive late). Other
// orgs are separate keys, so a slow org-A reply cannot reach org B's view.
// No generation arithmetic is used, so an operator restore is not masked.

let codingSeq = 0;
const codingAcceptedSeq = new Map<string, number>();

export function codingSettingsQueryKey(slug: string): readonly unknown[] {
  return ['settings', 'coding', slug] as const;
}

function acceptCodingView(slug: string): void {
  codingAcceptedSeq.set(slug, ++codingSeq);
}

function useCodingSettings(): CodingSettingsQueryLike {
  const slug = useRealOrgSlug();
  const qc = useQueryClient();
  const key = codingSettingsQueryKey(slug);
  const query = useQuery({
    queryKey: key,
    queryFn: async (): Promise<CodingSettingsView> => {
      const issued = ++codingSeq;
      // Settlement ordering applies to rejections too: a read issued before
      // a newer accepted write/readback can neither replace nor error out
      // that accepted view. Without an accepted view a failure stays visible.
      const obsolete = () => issued < (codingAcceptedSeq.get(slug) ?? 0);
      let data: CodingSettingsView;
      try {
        data = await settingsApi.getCodingSettings(slug);
      } catch (error) {
        const accepted = qc.getQueryData<CodingSettingsView>(key);
        if (obsolete() && accepted !== undefined) return accepted;
        throw error;
      }
      const accepted = qc.getQueryData<CodingSettingsView>(key);
      if (obsolete() && accepted !== undefined) return accepted;
      acceptCodingView(slug);
      return data;
    },
    enabled: !!slug,
    staleTime: 30_000,
    // A failed read is shown as a refusal; nothing is retried silently.
    retry: false,
  });
  return {
    data: query.data,
    isLoading: query.isLoading,
    isError: query.isError,
    error: query.error,
    isFetching: query.isFetching,
    refetch: () => query.refetch(),
  };
}

function useCodingSettingsOptions() {
  const slug = useRealOrgSlug();
  const query = useQuery({
    queryKey: ['settings', 'coding-options', slug],
    queryFn: () => settingsApi.getCodingSettingsOptions(slug),
    enabled: !!slug,
    staleTime: 30_000,
    retry: false,
  });
  return { data: query.data, isLoading: query.isLoading, isError: query.isError,
    error: query.error, isFetching: query.isFetching, refetch: () => query.refetch() };
}

// Send ownership is settings-local and lives with the provider's QueryClient,
// not with the mounted pane: leaving the route or switching org A->B->A keeps
// org A's frozen command/key and fence. It is never persisted.
interface CodingSendStore {
  fences: Map<string, CodingSendFence>;
  committed: Set<string>;
  listeners: Set<() => void>;
}
const codingSendStores = new WeakMap<QueryClient, CodingSendStore>();

function codingSendStore(qc: QueryClient): CodingSendStore {
  let store = codingSendStores.get(qc);
  if (!store) {
    store = { fences: new Map(), committed: new Set(), listeners: new Set() };
    codingSendStores.set(qc, store);
  }
  return store;
}

function setCodingFence(store: CodingSendStore, slug: string, fence: CodingSendFence | null): void {
  if (fence) store.fences.set(slug, fence);
  else store.fences.delete(slug);
  store.listeners.forEach((listener) => listener());
}

function useCodingSendFence(): CodingSendFence | null {
  const slug = useRealOrgSlug();
  const store = codingSendStore(useQueryClient());
  return useSyncExternalStore(
    (listener) => { store.listeners.add(listener); return () => { store.listeners.delete(listener); }; },
    () => store.fences.get(slug) ?? null,
  );
}

function useCodingSettingsActions(): CodingSettingsActions {
  const slug = useRealOrgSlug();
  const qc = useQueryClient();
  return useMemo(() => {
    const key = codingSettingsQueryKey(slug);
    const store = codingSendStore(qc);
    const owns = (operationKey: string) => store.fences.get(slug)?.command.operation_key === operationKey;
    const publish = (view: CodingSettingsView) => {
      acceptCodingView(slug);
      qc.setQueryData(key, view);
    };
    return {
      save: async (command: CodingSettingsCommand) => {
        // The fence is frozen synchronously, before any await, so a second
        // pane/click/remount cannot send a replacement.
        if (store.fences.has(slug)) throw new Error('coding_settings_send_in_progress');
        setCodingFence(store, slug, { command, phase: 'sending', detail: null });
        // Outstanding reads become obsolete; the PUT itself is sent once.
        await qc.cancelQueries({ queryKey: key });
        const saved = await settingsApi.putCodingSettings(slug, command);
        publish({ format: 'workflow-coding-settings-view@1', org_slug: saved.org_slug,
          current: saved.current, configuration: saved.configuration, operation: null });
        if (owns(command.operation_key)) setCodingFence(store, slug, null);
        return saved;
      },
      fence: () => store.fences.get(slug) ?? null,
      settle: (operationKey: string, uncertainDetail: string | null) => {
        const fence = store.fences.get(slug);
        if (!fence || fence.command.operation_key !== operationKey || fence.phase !== 'sending') return;
        setCodingFence(store, slug, uncertainDetail === null ? null : { ...fence, phase: 'uncertain', detail: uncertainDetail });
      },
      resolveCommitted: (operationKey: string) => {
        if (!owns(operationKey) || !store.committed.has(operationKey)) return;
        store.committed.delete(operationKey);
        setCodingFence(store, slug, null);
      },
      readOperation: async (operationKey: string) => {
        await qc.cancelQueries({ queryKey: key });
        const view = await settingsApi.getCodingSettings(slug, operationKey);
        if (view.operation?.operation_key === operationKey) store.committed.add(operationKey);
        // The receipt is returned to its caller; the cached view is current only.
        publish({ ...view, operation: null });
        return view;
      },
    };
  }, [qc, slug]);
}

export const realSettingsApi: SettingsApi = {
  useSettings,
  useUpdateOrgSettings,
  useDaemonCapacity,
  useUpdateDaemonCapacity,
  useNextWakes,
  useCodingSettings,
  useCodingSettingsOptions,
  useCodingSettingsActions,
  useCodingSendFence,
};
