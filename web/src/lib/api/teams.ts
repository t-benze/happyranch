/** Mirror of src/daemon/routes/teams.py */
import { request } from './client';

export interface TeamSummary {
  name: string;
  manager: string;
  workers: string[];
}

export const listTeams = (slug: string): Promise<{ teams: TeamSummary[] }> =>
  request(`/orgs/${slug}/teams`);
