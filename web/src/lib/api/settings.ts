import { request } from './client';
import type { SettingsSnapshot, OrgSettingsPatch } from './types';

export const getSettings = (slug: string): Promise<SettingsSnapshot> =>
  request(`/orgs/${slug}/settings`);

export const putOrgSettings = (
  slug: string,
  patch: OrgSettingsPatch,
): Promise<SettingsSnapshot> =>
  request(`/orgs/${slug}/settings/org`, { method: 'PUT', body: patch });

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
