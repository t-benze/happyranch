import { request } from './client';
import type { SettingsSnapshot, OrgSettingsPatch, NextWakesResponse } from './types';

export const getSettings = (slug: string): Promise<SettingsSnapshot> =>
  request(`/orgs/${slug}/settings`);

export const putOrgSettings = (
  slug: string,
  patch: OrgSettingsPatch,
): Promise<SettingsSnapshot> =>
  request(`/orgs/${slug}/settings/org`, { method: 'PUT', body: patch });

/** Preview the next N wake timestamps for an agent's resolved effective
 * schedule. Read-only; an incomplete/invalid schedule returns 200 with
 * `error` set and `next_wakes: []`. */
export const getNextWakes = (
  slug: string,
  agent: string,
  count = 5,
): Promise<NextWakesResponse> =>
  request(`/orgs/${slug}/work-hours/next-wakes`, { params: { agent, count } });

export interface TeamsPatchBody {
  team: string;
  add_workers?: string[];
  remove_workers?: string[];
}

export interface TeamRow {
  name: string;
  manager: string;
  workers: string[];
}

export const putTeams = (
  slug: string,
  patch: TeamsPatchBody,
): Promise<{ teams: TeamRow[] }> =>
  request(`/orgs/${slug}/settings/teams`, { method: 'PUT', body: patch });
