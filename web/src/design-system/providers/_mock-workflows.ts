/** Inert isolated sandbox contract. It never sends a response or allocates an author. */
import type { WorkflowsApi, WorkflowQuery } from './DataContext';
function empty<T>(data?: T): WorkflowQuery<T> {
  return { data, isLoading: false, isError: false, error: null, isFetching: false, refetch: async () => undefined };
}
export const mockWorkflowsApi: WorkflowsApi = {
  useWorkflowSetupStatus: () => empty({ format: 'workflow-setup-status@1', org_slug: 'sandbox',
    state: 'unknown', reason: 'evidence_unavailable', attempt_time: null, backup_path: null, integrity_report: null }),
  useSequentialSettingsFence: () => null,
  useSequentialSettingsActions: () => ({
    save: async () => { throw new Error('workflow_display_only'); },
    readOperation: async () => { throw new Error('workflow_display_only'); },
  }),
  useSequentialDefinitions: () => empty(), useSequentialDefinition: () => empty(),
  useSequentialSettings: () => empty(), useSequentialRuns: () => empty(),
  useSequentialRun: () => empty(), useTaskSequentialWorkflow: () => empty(),
};
