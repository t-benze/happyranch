/**
 * Mock `TeamsApi` for the prototype harness.
 *
 * The mock org has the same two teams as the real sample to keep prototype
 * compositions visually consistent.
 */
import { useQuery } from '@tanstack/react-query';
import type { TeamSummary } from '@/lib/api/teams';
import type { TeamsApi } from './DataContext';

const MOCK_TEAMS: TeamSummary[] = [
  { name: 'content', manager: 'content_manager', workers: ['content_writer', 'content_qa'] },
  { name: 'engineering', manager: 'engineering_head', workers: ['product_manager', 'dev_agent'] },
];

export const mockTeamsApi: TeamsApi = {
  useTeamsList: () =>
    useQuery({
      queryKey: ['mock-teams'],
      queryFn: async (): Promise<{ teams: TeamSummary[] }> => ({ teams: MOCK_TEAMS }),
      staleTime: Infinity,
    }),
};
