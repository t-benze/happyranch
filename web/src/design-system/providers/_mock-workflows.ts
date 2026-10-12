/** Inert isolated sandbox contract. It never sends a response or allocates an author. */
import type { WorkflowsApi, WorkflowQuery } from './DataContext';
function empty<T>(data?: T): WorkflowQuery<T> {
  return { data, isLoading: false, isError: false, error: null, isFetching: false, refetch: async () => undefined };
}
export const mockWorkflowsApi: WorkflowsApi = {
  useActivations: () => empty([]), useReviewDetail: () => ({ ...empty(), captureAdmission: () => () => false }),
  useHumanResponse: () => ({ isPending: false, mutateAsync: async () => { throw new Error('workflow_display_only'); } }),
  useOwnerReturn: () => ({ isPending: false, mutateAsync: async () => { throw new Error('workflow_display_only'); } }),
};
