/** Mirror of the Founder-only cutover service; accepted pending is not enabled. */
import { request } from './client';
import type {
  WorkflowCutoverProjection,
  WorkflowCutoverRequestInput,
  WorkflowCutoverRequestResponse,
  WorkflowCutoverDowngradePreflight,
} from './types';

export const getWorkflowCutover = (slug: string): Promise<WorkflowCutoverProjection> =>
  request(`/orgs/${slug}/workflows/cutover`);

export const requestWorkflowCutover = (
  slug: string, body: WorkflowCutoverRequestInput,
): Promise<WorkflowCutoverRequestResponse> =>
  request(`/orgs/${slug}/workflows/cutover/requests`, { method: 'POST', body });

export const getWorkflowCutoverDowngradePreflight = (
  slug: string,
): Promise<WorkflowCutoverDowngradePreflight> =>
  request(`/orgs/${slug}/workflows/cutover/downgrade-preflight`);
