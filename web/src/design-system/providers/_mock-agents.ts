/**
 * Mock `AgentsApi` for the prototype harness. Returns canned roster
 * from `@/mocks/agents.ts`. Synchronous-ish — wrapped in useQuery to
 * mirror the real shape, so loading-state JSX in compositions still
 * runs. Mutations are no-ops that resolve immediately.
 */
import { useMutation, useQuery } from '@tanstack/react-query';
import type {
  AgentEnrollment,
  AgentSummary,
  LearningEntrySummary,
} from '@/lib/api/agents';
import type { TaskRecord } from '@/lib/api/types';
import { MOCK_AGENTS, MOCK_ENROLLMENTS } from '@/mocks';
import type { AgentsApi } from './DataContext';

export const mockAgentsApi: AgentsApi = {
  useAgentsList: () =>
    useQuery({
      queryKey: ['mock-agents'],
      queryFn: async (): Promise<{ agents: AgentSummary[] }> => ({ agents: MOCK_AGENTS }),
      staleTime: Infinity,
    }),

  useEnrollmentsList: (params) =>
    useQuery({
      queryKey: ['mock-agent-enrollments', params],
      queryFn: async (): Promise<{ enrollments: AgentEnrollment[] }> => ({
        enrollments: params?.status
          ? MOCK_ENROLLMENTS.filter((e) => e.status === params.status)
          : MOCK_ENROLLMENTS,
      }),
      staleTime: Infinity,
    }),

  useAgentLearnings: (agentName) =>
    useQuery({
      queryKey: ['mock-agent-learnings', agentName],
      queryFn: async (): Promise<{ entries: LearningEntrySummary[] }> => ({
        entries: [],
      }),
      enabled: !!agentName,
      staleTime: Infinity,
    }),

  useAgentTasks: (agentName) =>
    useQuery({
      queryKey: ['mock-agent-tasks', agentName],
      queryFn: async (): Promise<{ tasks: TaskRecord[] }> => ({ tasks: [] }),
      enabled: !!agentName,
      staleTime: Infinity,
    }),

  useCreateAgent: () =>
    useMutation({
      mutationFn: async (body: import('@/lib/api/agents').CreateAgentBody) => ({
        name: body.name,
        team: body.team ?? body.new_team ?? '',
        role: body.role,
      }),
    }),

  useApproveAgent: () =>
    useMutation({
      mutationFn: async (_agentName: string) => ({ name: _agentName }),
    }),

  useRejectAgent: () =>
    useMutation({
      mutationFn: async ({ agentName }: { agentName: string }) => ({ name: agentName }),
    }),

  useSetAgentExecutor: () =>
    useMutation({
      mutationFn: async ({
        body,
      }: {
        agentName: string;
        body: import('./DataContext').SetAgentExecutorArgs;
      }) => ({
        agent: '',
        before: { org_executor: null, workspace_executor: null },
        after: { org_executor: body.executor, workspace_executor: body.executor },
        stale_files: [],
      }),
    }),

  useManageAgentRepo: () =>
    useMutation({
      mutationFn: async () => ({ ok: true as const }),
    }),
};
