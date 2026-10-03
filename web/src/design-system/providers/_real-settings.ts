/**
 * Real (daemon-backed) implementation of `SettingsApi`.
 *
 * Phase 1: read-only `useSettings` hook backed by GET /settings.
 * Phase 2: editable `useUpdateOrgSettings` mutation backed by PUT /settings/org.
 */
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useParams } from 'react-router-dom';
import { settings as settingsApi } from '@/lib/api';
import type {
  DaemonCapacitySnapshot,
  NextWakesResponse,
  OrgSettingsPatch,
  SettingsSnapshot,
  DaemonCapacityWrite,
} from '@/lib/api/types';
import type { SettingsApi, QueryLike } from './DataContext';
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

export const realSettingsApi: SettingsApi = {
  useSettings,
  useUpdateOrgSettings,
  useDaemonCapacity,
  useUpdateDaemonCapacity,
  useNextWakes,
};
