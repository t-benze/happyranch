/** Selected-recipient review request and history reads. */
import { request } from './client';
import type { WorkflowReviewRequestProof, WorkflowReviewRequestReceipt } from './types';

export const getWorkflowReviewRequest = (slug: string, requestId: string, proof: WorkflowReviewRequestProof, signal?: AbortSignal): Promise<WorkflowReviewRequestReceipt> =>
  request(`/orgs/${slug}/workflows/reviews/requests/${encodeURIComponent(requestId)}`, { params: proof, signal });
