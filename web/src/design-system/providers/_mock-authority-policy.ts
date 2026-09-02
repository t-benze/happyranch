import type { AuthorityPolicyApi } from './DataContext';

export const mockAuthorityPolicyApi: AuthorityPolicyApi = {
  useTeamEscalationPolicy: () => ({
    data: undefined,
    isLoading: false,
    isError: false,
    error: null,
  }),
  useCreateTeamEscalationPolicyRelease: () => ({ mutateAsync: async () => { throw new Error('Unavailable in prototype'); }, isPending: false }),
  useActivateTeamEscalationPolicyRelease: () => ({ mutateAsync: async () => { throw new Error('Unavailable in prototype'); }, isPending: false }),
};
