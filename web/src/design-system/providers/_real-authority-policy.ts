import { useMutation, useQueries, useQueryClient } from '@tanstack/react-query';
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
    return useMutation({
      mutationFn: ({ agentName, body }) => authorityPolicyApi.createTeamEscalationPolicyRelease(slug, agentName, body),
    });
  },
  useActivateTeamEscalationPolicyRelease: () => {
    const { slug = '' } = useParams<{ slug: string }>();
    const qc = useQueryClient();
    return useMutation({
      mutationFn: ({ agentName, body }) => authorityPolicyApi.activateTeamEscalationPolicyRelease(slug, agentName, body),
      onSuccess: (_data, { agentName }) => qc.invalidateQueries({ queryKey: ['team-escalation-policy', slug, agentName] }),
    });
  },
};
