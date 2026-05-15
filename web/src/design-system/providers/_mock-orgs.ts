/**
 * Mock `OrgsApi`. Returns the canonical fixture from `@/mocks` with no
 * network call.
 */
import { useQuery } from '@tanstack/react-query';
import { MOCK_ORGS } from '@/mocks';
import type { OrgsApi } from './DataContext';

export const mockOrgsApi: OrgsApi = {
  useOrgsList: () =>
    useQuery({
      queryKey: ['mock-orgs'],
      queryFn: async () => ({ orgs: MOCK_ORGS }),
    }),
};
