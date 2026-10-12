/** Current selected-recipient projection; pending requests carry no signature. */
import { request } from './client';
import type { WorkflowReviewRequestProof, WorkflowReviewRequestReceipt } from './types';

export const getWorkflowReviewRequest = (slug: string, requestId: string, proof: WorkflowReviewRequestProof): Promise<WorkflowReviewRequestReceipt> =>
  request(`/orgs/${slug}/workflows/reviews/requests/${encodeURIComponent(requestId)}`, { params: proof });
