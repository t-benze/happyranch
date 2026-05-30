/**
 * Real (daemon-backed) `TeamsApi`. Private to the providers folder.
 */
import { useQuery } from '@tanstack/react-query';
import { useParams } from 'react-router-dom';
import { teams as teamsApi } from '@/lib/api';
import type { TeamsApi } from './DataContext';

function useRealOrgSlug(): string {
  const { slug } = useParams<{ slug: string }>();
  return slug ?? '';
}

export const realTeamsApi: TeamsApi = {
  useTeamsList: () => {
    const slug = useRealOrgSlug();
    return useQuery({
      queryKey: ['teams', slug],
      queryFn: () => teamsApi.listTeams(slug),
      enabled: !!slug,
      staleTime: 5 * 60 * 1000,
    });
  },
};
