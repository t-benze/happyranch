/** Mirror of src/daemon/routes/teams.py */
import { request } from './client';

export interface TeamSummary {
  name: string;
  manager: string | null;
  manager_kind?: 'agent' | 'human';
  human_manager?: 'founder' | null;
  is_default?: boolean;
  workers: string[];
}

export const listTeams = (slug: string): Promise<{ teams: TeamSummary[] }> =>
  request(`/orgs/${slug}/teams`);

export interface EnsureDefaultResult {
  created: boolean;
  team: TeamSummary;
  default_team: string;
  task_default_team: string;
  authority:
    | { ready: true; namespace: string; generation: number; snapshot_digest: string }
    | { ready: false; code: 'default_team_authority_not_ready'; reason: string; message: string };
}

/** Explicit founder operation. A committed roster with ready=false is incomplete. */
export const ensureDefaultTeam = (slug: string): Promise<EnsureDefaultResult> =>
  request(`/orgs/${slug}/teams/ensure-default`, { method: 'POST' });
