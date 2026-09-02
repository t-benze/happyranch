import type { AuthorityPolicyApi } from './DataContext';

export const mockAuthorityPolicyApi: AuthorityPolicyApi = {
  useTeamEscalationPolicy: () => ({
    data: undefined,
    isLoading: false,
    isError: false,
    error: null,
  }),
};
