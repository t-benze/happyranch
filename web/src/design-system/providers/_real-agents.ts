/**
 * Real (daemon-backed) `AgentsApi`. Private to the providers folder —
 * compositions go through `@/hooks/agents`.
 *
 * The slug is read from URL params via `useParams` so the public hook
 * shape stays provider-agnostic. Five-minute staleTime since the org
 * roster changes infrequently within a session.
 */
import { useQuery } from '@tanstack/react-query';
import { useParams } from 'react-router-dom';
import { agents as agentsApi } from '@/lib/api';
import type { AgentsApi } from './DataContext';

export const realAgentsApi: AgentsApi = {
  useAgentsList: () => {
    const { slug } = useParams<{ slug: string }>();
    return useQuery({
      queryKey: ['agents', slug],
      queryFn: () => agentsApi.listAgents(slug as string),
      enabled: !!slug,
      staleTime: 5 * 60 * 1000,
    });
  },
};
