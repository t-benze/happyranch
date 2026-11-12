/** Inert isolated sandbox contract. It never sends a response or allocates an author. */
import type { WorkflowsApi, WorkflowQuery } from './DataContext';
function empty<T>(data?: T): WorkflowQuery<T> {
  return { data, isLoading: false, isError: false, error: null, isFetching: false, refetch: async () => undefined };
}
export const mockWorkflowsApi: WorkflowsApi = {
  useSequentialSettingsFence: () => null,
  useSequentialSettingsActions: () => ({
    save: async () => { throw new Error('workflow_display_only'); },
    readOperation: async () => { throw new Error('workflow_display_only'); },
  }),
  useSequentialDefinitions: () => empty(), useSequentialDefinition: () => empty(),
  useSequentialSettings: () => empty(), useSequentialRuns: () => empty(),
  useSequentialRun: () => empty(), useTaskSequentialWorkflow: () => empty(),
};
