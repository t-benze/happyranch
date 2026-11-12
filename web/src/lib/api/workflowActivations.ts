/** Original activation receipts and separate current eligibility; no host controls. */
import { request } from './client';
import type { WorkflowActivationRequest, WorkflowActivationReceipt } from './types';

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
