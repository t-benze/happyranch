/**
 * Real (daemon-backed) `OrgsApi`. Private to the providers folder.
 */
import { useQuery } from '@tanstack/react-query';
import { orgs as orgsApi } from '@/lib/api';
import type { OrgsApi } from './DataContext';

export const realOrgsApi: OrgsApi = {
  useOrgsList: () => useQuery({ queryKey: ['orgs'], queryFn: orgsApi.listOrgs }),
};
