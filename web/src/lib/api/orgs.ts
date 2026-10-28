/** Mirror of src/daemon/routes/orgs.py */
import { request } from './client';
import type { WorkflowPhysicalHealth } from './types';

export interface OrgSummary {
  slug: string;
  root: string;
  workflow_physical_health?: WorkflowPhysicalHealth | null;
}

export interface BrokenOrg {
  slug: string;
  error: string;
}

export const listOrgs = (): Promise<{ orgs: OrgSummary[]; broken: BrokenOrg[] }> =>
  request('/orgs');

export const createOrg = (body: {
  slug: string;
  from_example?: string;
}): Promise<{ slug: string }> =>
  request('/orgs', { method: 'POST', body });

export const unloadOrg = (slug: string): Promise<{ slug: string }> =>
  request(`/orgs/${slug}`, { method: 'DELETE' });
