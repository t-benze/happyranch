import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useParams } from 'react-router-dom';
import { identities } from '@/lib/api';
import { useOrgSlugOptional } from '@/lib/orgSlug';
import type { AgentSummary, AgentEnrollment, IdentityList, IdentityView } from '@/lib/api/types';
import type { IdentitiesApi } from './DataContext';

export const identityKey = (slug: string) => ['identities', slug] as const;
function useSlug(): string {
  const scoped = useOrgSlugOptional();
  const { slug } = useParams<{ slug: string }>();
  return scoped ?? slug ?? '';
}

function refreshSummaryMetadata(qc: ReturnType<typeof useQueryClient>, slug: string, identities: IdentityView[]) {
  const named = new Map(identities.filter((i) => i.kind === 'agent').map((i) => [i.canonical_id, i]));
  const metadata = (id: string) => {
    const i = named.get(id);
    return i ? { addressable_name: i.addressable_name, name_revision: i.name_revision, naming_status: i.naming_status } : {};
  };
  qc.setQueriesData<{ agents: AgentSummary[] }>({ queryKey: ['agents', slug] }, (cached) => cached && ({ ...cached,
    agents: cached.agents.map((a) => ({ ...a, ...metadata(a.name) })),
  }));
  qc.setQueriesData<{ enrollments: AgentEnrollment[] }>({ queryKey: ['agent-enrollments', slug] }, (cached) => cached && ({ ...cached,
    enrollments: cached.enrollments.map((a) => ({ ...a, ...metadata(a.name) })),
  }));
}

export const realIdentitiesApi: IdentitiesApi = {
  useIdentities: () => {
    const slug = useSlug();
    return useQuery({
      queryKey: identityKey(slug), queryFn: ({ signal }) => identities.listIdentities(slug, signal),
      enabled: !!slug, staleTime: 30_000, refetchInterval: 30_000,
    });
  },
  useReadIdentities: () => {
    const qc = useQueryClient();
    return useMutation({
      mutationFn: async ({ slug }: { slug: string }) => {
        await qc.cancelQueries({ queryKey: identityKey(slug), exact: true }, { revert: false });
        const observation = qc.getQueryState(identityKey(slug))?.dataUpdateCount;
        const fresh = await identities.listIdentities(slug);
        const cached = qc.getQueryData<IdentityList>(identityKey(slug));
        if (qc.getQueryState(identityKey(slug))?.dataUpdateCount !== observation && cached?.identities.some((i) => {
          const observed = fresh.identities.find((f) => f.kind === i.kind && f.canonical_id === i.canonical_id);
          return observed && (i.name_revision ?? 0) > (observed.name_revision ?? 0);
        })) throw new Error('Identity changed during readback');
        qc.setQueryData(identityKey(slug), fresh);
        refreshSummaryMetadata(qc, slug, fresh.identities);
        return fresh;
      }, retry: false,
    });
  },
  useRenameIdentity: () => {
    const qc = useQueryClient();
    return useMutation({
      mutationFn: async ({ slug, kind, canonicalId, body }: Parameters<ReturnType<IdentitiesApi['useRenameIdentity']>['mutateAsync']>[0]) => {
        await qc.cancelQueries({ queryKey: identityKey(slug), exact: true }, { revert: false });
        const result = kind === 'founder'
          ? await identities.renameFounder(slug, body)
          : await identities.renameAgent(slug, canonicalId, body);
        if (result.kind !== kind || result.canonical_id !== canonicalId) throw new Error('Identity receipt mismatch');
        qc.setQueryData<IdentityList>(identityKey(slug), (cached) => cached && ({ identities: cached.identities.map((i: IdentityView) =>
          i.kind === kind && i.canonical_id === canonicalId && (i.name_revision ?? 0) <= (result.name_revision ?? 0) ? result : i) }));
        refreshSummaryMetadata(qc, slug, [result]);
        // Refresh metadata-bearing reads only; drafts/settings are not reset by rename.
        void qc.invalidateQueries({ queryKey: identityKey(slug) });
        return result;
      }, retry: false,
    });
  },
  usePreflightAddresses: () => useMutation({ mutationFn: identities.preflightAddresses, retry: false }),
};
