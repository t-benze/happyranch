import { useInfiniteQuery, useMutation, useQueries, useQueryClient } from '@tanstack/react-query';
import { useParams } from 'react-router-dom';
import * as authorityPolicyApi from '@/lib/api/authorityPolicy';
import type { AuthorityPolicyApi } from './DataContext';

export const realAuthorityPolicyApi: AuthorityPolicyApi = {
  useTeamEscalationPolicy: (agent) => {
    const { slug = '' } = useParams<{ slug: string }>();
    const enabled = !!slug && authorityPolicyApi.isEligiblePolicyManager(agent);
    const queryOptions: Array<{
      queryKey: string[];
      queryFn: () => Promise<authorityPolicyApi.TeamEscalationPolicyResponse>;
      retry: false;
    }> = enabled
      ? [{
          queryKey: ['team-escalation-policy', slug, agent!.name],
          queryFn: () => authorityPolicyApi.getTeamEscalationPolicy(slug, agent!.name),
          retry: false,
        }]
      : [];
    const queries = useQueries({
      queries: queryOptions,
    });
    return queries[0] ?? {
      data: undefined,
      isLoading: false,
      isError: false,
      error: null,
      refetch: async () => undefined,
    };
  },
  useCreateTeamEscalationPolicyRelease: () => {
    const { slug = '' } = useParams<{ slug: string }>();
    const qc = useQueryClient();
    return useMutation({
      mutationFn: ({ agentName, body }) => authorityPolicyApi.createTeamEscalationPolicyRelease(slug, agentName, body),
      onSuccess: async (_data, { agentName }) => {
        await Promise.all([
          qc.invalidateQueries({ queryKey: ['team-escalation-policy', slug, agentName] }),
          qc.invalidateQueries({ queryKey: ['team-escalation-policy-history', slug, agentName] }),
        ]);
      },
    });
  },
  useActivateTeamEscalationPolicyRelease: () => {
    const { slug = '' } = useParams<{ slug: string }>();
    const qc = useQueryClient();
    return useMutation({
      mutationFn: ({ agentName, body }) => authorityPolicyApi.activateTeamEscalationPolicyRelease(slug, agentName, body),
      onSuccess: async (_data, { agentName }) => {
        await Promise.all([
          qc.invalidateQueries({ queryKey: ['team-escalation-policy', slug, agentName] }),
          qc.invalidateQueries({ queryKey: ['team-escalation-policy-history', slug, agentName] }),
        ]);
      },
    });
  },
  useCreateTeamEscalationPolicyV2Release: () => {
    const { slug = '' } = useParams<{ slug: string }>();
    const qc = useQueryClient();
    return useMutation({
      mutationFn: ({ agentName, body }) =>
        authorityPolicyApi.createAndActivateTeamEscalationPolicyV2(slug, agentName, body),
      onSuccess: async (_data, { agentName }) => {
        // The paired control selects a v2 family, so the current projection and
        // the family-specific v2 history change; the legacy stream is untouched
        // and keeps its own independent pagination.
        await Promise.all([
          qc.invalidateQueries({ queryKey: ['team-escalation-policy', slug, agentName] }),
          qc.invalidateQueries({ queryKey: ['team-escalation-policy-v2-history', slug, agentName] }),
        ]);
      },
    });
  },
  useActivateTeamEscalationPolicyV2Release: () => {
    const { slug = '' } = useParams<{ slug: string }>();
    const qc = useQueryClient();
    return useMutation({
      mutationFn: ({ agentName, body }) =>
        authorityPolicyApi.activateTeamEscalationPolicyV2(slug, agentName, body),
      onSuccess: async (_data, { agentName }) => {
        await Promise.all([
          qc.invalidateQueries({ queryKey: ['team-escalation-policy', slug, agentName] }),
          qc.invalidateQueries({ queryKey: ['team-escalation-policy-v2-history', slug, agentName] }),
        ]);
      },
    });
  },
  useTeamEscalationPolicyHistory: (agent) => {
    const { slug = '' } = useParams<{ slug: string }>();
    const enabled = !!slug && authorityPolicyApi.isEligiblePolicyManager(agent);
    const q = useInfiniteQuery({
      queryKey: ['team-escalation-policy-history', slug, agent?.name], initialPageParam: undefined as string | undefined,
      queryFn: ({ pageParam }) => authorityPolicyApi.getTeamEscalationPolicyHistory(slug, agent!.name, pageParam),
      getNextPageParam: (last) => last.next_cursor ?? undefined, enabled, retry: false,
    });
    return { data: q.data ? { pages: q.data.pages } : undefined, isLoading: q.isLoading,
      isError: q.isError, error: (q.error as Error | null) ?? null,
      fetchNextPage: () => q.fetchNextPage(), hasNextPage: !!q.hasNextPage,
      isFetchingNextPage: q.isFetchingNextPage };
  },
  useTeamEscalationPolicyV2History: (agent) => {
    const { slug = '' } = useParams<{ slug: string }>();
    const enabled = !!slug && authorityPolicyApi.isEligiblePolicyManager(agent);
    const q = useInfiniteQuery({
      queryKey: ['team-escalation-policy-v2-history', slug, agent?.name], initialPageParam: undefined as string | undefined,
      queryFn: ({ pageParam }) => authorityPolicyApi.getTeamEscalationPolicyV2History(slug, agent!.name, pageParam),
      getNextPageParam: (last) => last.next_cursor ?? undefined, enabled, retry: false,
    });
    return { data: q.data ? { pages: q.data.pages } : undefined, isLoading: q.isLoading,
      isError: q.isError, error: (q.error as Error | null) ?? null,
      fetchNextPage: () => q.fetchNextPage(), hasNextPage: !!q.hasNextPage,
      isFetchingNextPage: q.isFetchingNextPage };
  },
  useTeamEscalationPolicyOutcomes: (agent) => {
    const { slug = '' } = useParams<{ slug: string }>();
    const enabled = !!slug && authorityPolicyApi.isEligiblePolicyManager(agent);
    const q = useInfiniteQuery({
      queryKey: ['team-escalation-policy-outcomes', slug, agent?.name], initialPageParam: undefined as string | undefined,
      queryFn: ({ pageParam }) => authorityPolicyApi.getTeamEscalationPolicyOutcomes(slug, agent!.name, pageParam),
      getNextPageParam: (last) => last.next_cursor ?? undefined, enabled, retry: false,
    });
    return { data: q.data ? { pages: q.data.pages } : undefined, isLoading: q.isLoading,
      isError: q.isError, error: (q.error as Error | null) ?? null,
      fetchNextPage: () => q.fetchNextPage(), hasNextPage: !!q.hasNextPage,
      isFetchingNextPage: q.isFetchingNextPage };
  },
};
