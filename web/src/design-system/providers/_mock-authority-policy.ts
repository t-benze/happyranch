import type { AuthorityPolicyApi } from './DataContext';

export const mockAuthorityPolicyApi: AuthorityPolicyApi = {
  useTeamEscalationPolicy: () => ({
    data: undefined,
    isLoading: false,
    isError: false,
    error: null,
    refetch: async () => undefined,
  }),
  useCreateTeamEscalationPolicyRelease: () => ({ mutateAsync: async () => { throw new Error('Unavailable in prototype'); }, isPending: false }),
  useActivateTeamEscalationPolicyRelease: () => ({ mutateAsync: async () => { throw new Error('Unavailable in prototype'); }, isPending: false }),
  useTeamEscalationPolicyHistory: () => ({ data: { items: [], next_cursor: null }, isLoading: false, isError: false, error: null }),
  useTeamEscalationPolicyOutcomes: () => ({ data: { items: [], next_cursor: null }, isLoading: false, isError: false, error: null }),
};
