/**
 * Mock `AgentsApi` for the prototype harness. Returns canned roster
 * from `@/mocks/agents.ts`. Synchronous-ish — wrapped in useQuery to
 * mirror the real shape, so loading-state JSX in compositions still
 * runs.
 */
import { useQuery } from '@tanstack/react-query';
import type { AgentSummary } from '@/lib/api/agents';
import { MOCK_AGENTS } from '@/mocks';
import type { AgentsApi } from './DataContext';

export const mockAgentsApi: AgentsApi = {
  useAgentsList: () =>
    useQuery({
      queryKey: ['mock-agents'],
      queryFn: async (): Promise<{ agents: AgentSummary[] }> => ({ agents: MOCK_AGENTS }),
      staleTime: Infinity,
    }),
};
