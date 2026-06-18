/**
 * Public, provider-aware agents hooks. Compositions import from here.
 * One-line forwarders into `useData().agents` so the same JSX renders
 * against either AppProvider or PrototypeProvider.
 */
import { useData } from '@/design-system/providers/DataContext';

export const useAgentsList: ReturnType<typeof useData>['agents']['useAgentsList'] = () =>
  useData().agents.useAgentsList();

export const useEnrollmentsList: ReturnType<typeof useData>['agents']['useEnrollmentsList'] = (
  params,
) => useData().agents.useEnrollmentsList(params);

export const useAgentLearnings: ReturnType<typeof useData>['agents']['useAgentLearnings'] = (
  agentName,
) => useData().agents.useAgentLearnings(agentName);

export const useAgentTasks: ReturnType<typeof useData>['agents']['useAgentTasks'] = (
  agentName,
) => useData().agents.useAgentTasks(agentName);

export const useApproveAgent: ReturnType<typeof useData>['agents']['useApproveAgent'] = () =>
  useData().agents.useApproveAgent();

export const useRejectAgent: ReturnType<typeof useData>['agents']['useRejectAgent'] = () =>
  useData().agents.useRejectAgent();

export const useCreateAgent: ReturnType<typeof useData>['agents']['useCreateAgent'] = () =>
  useData().agents.useCreateAgent();

export const useSetAgentExecutor: ReturnType<typeof useData>['agents']['useSetAgentExecutor'] = () =>
  useData().agents.useSetAgentExecutor();

export const useManageAgentRepo: ReturnType<typeof useData>['agents']['useManageAgentRepo'] = () =>
  useData().agents.useManageAgentRepo();

export const useAgentsRoutes = () => useData().useAgentsRoutes();
