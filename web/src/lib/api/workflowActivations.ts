/** Original activation receipts and separate current eligibility; no host controls. */
import { request } from './client';
import type { WorkflowActivationRequest, WorkflowActivationReceipt, OwnerReviewSelection } from './types';

export const activateWorkflow = (
  slug: string, body: WorkflowActivationRequest,
): Promise<WorkflowActivationReceipt> =>
  request(`/orgs/${slug}/workflows/activations`, { method: 'POST', body });

export const listWorkflowActivations = (slug: string, signal?: AbortSignal): Promise<WorkflowActivationReceipt[]> =>
  request(`/orgs/${slug}/workflows/activations`, { signal });

export const getWorkflowActivation = (
  slug: string, activationId: string, signal?: AbortSignal,
): Promise<WorkflowActivationReceipt> =>
  request(`/orgs/${slug}/workflows/activations/${encodeURIComponent(activationId)}`, { signal });


/** Old daemons fail normally; callers must show discovery unavailable and disable dependent actions. */
export const getOwnerReviewSelection = (
  slug: string, activationId: string, signal?: AbortSignal,
): Promise<OwnerReviewSelection> =>
  request(`/orgs/${slug}/workflows/activations/${encodeURIComponent(activationId)}/review-selection`, {
    params: { proof_kind: 'owner-human' }, signal,
  });
