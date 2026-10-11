/** Current selected-recipient reads and existing Founder-bearer human writes. */
import { request } from './client';
import type { HumanReviewCommand, HumanReviewReceipt, WorkflowReviewRequestProof, WorkflowReviewRequestReceipt } from './types';

export const getWorkflowReviewRequest = (slug: string, requestId: string, proof: WorkflowReviewRequestProof): Promise<WorkflowReviewRequestReceipt> =>
  request(`/orgs/${slug}/workflows/reviews/requests/${encodeURIComponent(requestId)}`, { params: proof });

export const signHumanReview = (slug: string, body: HumanReviewCommand): Promise<HumanReviewReceipt> =>
  request(`/orgs/${slug}/workflows/reviews/human-receipts`, { method: 'POST', body });
