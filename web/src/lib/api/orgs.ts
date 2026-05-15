/** Mirror of src/daemon/routes/orgs.py */
import { request } from './client';

export interface OrgSummary {
  slug: string;
  root: string;
}

export const listOrgs = (): Promise<{ orgs: OrgSummary[] }> => request('/orgs');

export const createOrg = (body: {
  slug: string;
  from_example?: string;
}): Promise<{ slug: string }> =>
  request('/orgs', { method: 'POST', body });

export const unloadOrg = (slug: string): Promise<{ slug: string }> =>
  request(`/orgs/${slug}`, { method: 'DELETE' });
