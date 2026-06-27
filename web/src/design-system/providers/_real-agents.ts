/**
 * Real (daemon-backed) `AgentsApi`. Private to the providers folder —
 * compositions go through `@/hooks/agents`.
 *
 * The slug is read from URL params via `useParams` so the public hook
 * shape stays provider-agnostic. Five-minute staleTime since the org
 * roster changes infrequently within a session; enrollments use a
 * shorter staleTime because pending → approved transitions need to
 * reflect quickly after the founder clicks Approve.
 */
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useParams } from 'react-router-dom';
import { agents as agentsApi, tasks as tasksApi } from '@/lib/api';
import type { TaskRecord } from '@/lib/api/types';
import type {
  AgentsApi,
  ApproveAgentArgs,
  ApproveAgentResult,
  CreateAgentArgs,
  CreateAgentResult,
  RejectAgentResult,
} from './DataContext';

function useRealOrgSlug(): string {
  const { slug } = useParams<{ slug: string }>();
  return slug ?? '';
}

export const realAgentsApi: AgentsApi = {
  useAgentsList: () => {
    const slug = useRealOrgSlug();
    return useQuery({
      queryKey: ['agents', slug],
      queryFn: () => agentsApi.listAgents(slug),
      enabled: !!slug,
      staleTime: 5 * 60 * 1000,
    });
  },

  useEnrollmentsList: (params) => {
    const slug = useRealOrgSlug();
    return useQuery({
      queryKey: ['agent-enrollments', slug, params],
      queryFn: () => agentsApi.listEnrollments(slug, params),
      enabled: !!slug,
      staleTime: 30_000,
    });
  },

  useAgentLearnings: (agentName) => {
    const slug = useRealOrgSlug();
    return useQuery({
      queryKey: ['agent-learnings', slug, agentName],
      queryFn: () => agentsApi.listMemory(slug, agentName as string),
      enabled: !!slug && !!agentName,
      // 412 (workspace_not_migrated) is a legitimate state — let the
      // caller render an empty-state hint rather than retrying forever.
      retry: false,
    });
  },

  useAgentTasks: (agentName) => {
    const slug = useRealOrgSlug();
    return useQuery({
      queryKey: ['agent-tasks', slug, agentName],
      queryFn: () =>
        tasksApi.listTasks(slug, {
          assigned_agent: agentName as string,
          limit: 20,
        }) as Promise<{ tasks: TaskRecord[] }>,
      enabled: !!slug && !!agentName,
    });
  },

  useCreateAgent: () => {
    const slug = useRealOrgSlug();
    const qc = useQueryClient();
    return useMutation({
      mutationFn: (body: CreateAgentArgs): Promise<CreateAgentResult> =>
        agentsApi.createAgent(slug, body),
      onSuccess: () => {
        qc.invalidateQueries({ queryKey: ['agents', slug] });
        qc.invalidateQueries({ queryKey: ['agent-enrollments', slug] });
        qc.invalidateQueries({ queryKey: ['teams', slug] });
      },
    });
  },

  useApproveAgent: () => {
    const slug = useRealOrgSlug();
    const qc = useQueryClient();
    return useMutation({
      mutationFn: (agentName: ApproveAgentArgs): Promise<ApproveAgentResult> =>
        agentsApi.approveAgent(slug, agentName),
      onSuccess: () => {
        qc.invalidateQueries({ queryKey: ['agents', slug] });
        qc.invalidateQueries({ queryKey: ['agent-enrollments', slug] });
      },
    });
  },

  useRejectAgent: () => {
    const slug = useRealOrgSlug();
    const qc = useQueryClient();
    return useMutation({
      mutationFn: ({
        agentName,
        body,
      }: {
        agentName: string;
        body?: { reason?: string };
      }): Promise<RejectAgentResult> =>
        agentsApi.rejectAgent(slug, agentName, body),
      onSuccess: () => {
        qc.invalidateQueries({ queryKey: ['agent-enrollments', slug] });
      },
    });
  },

  useSetAgentExecutor: () => {
    const slug = useRealOrgSlug();
    const qc = useQueryClient();
    return useMutation({
      mutationFn: ({
        agentName,
        body,
      }: {
        agentName: string;
        body: import('./DataContext').SetAgentExecutorArgs;
      }): Promise<import('./DataContext').SetAgentExecutorResult> =>
        agentsApi.setAgentExecutor(slug, agentName, body),
      onSuccess: () => {
        qc.invalidateQueries({ queryKey: ['agents', slug] });
      },
    });
  },

  useManageAgentRepo: () => {
    const slug = useRealOrgSlug();
    const qc = useQueryClient();
    return useMutation({
      mutationFn: ({
        agentName,
        body,
      }: {
        agentName: string;
        body: import('./DataContext').ManageAgentRepoArgs;
      }): Promise<import('./DataContext').ManageAgentRepoResult> =>
        agentsApi.manageAgentRepo(slug, agentName, body),
      onSuccess: () => {
        qc.invalidateQueries({ queryKey: ['agents', slug] });
      },
    });
  },
};
