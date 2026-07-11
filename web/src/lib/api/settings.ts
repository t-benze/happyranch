import { request } from './client';
import type { SettingsSnapshot, OrgSettingsPatch, NextWakesResponse } from './types';

// ── Registration token mint (THR-052 PR-3) ──

export interface RegistrationTokenMintRequest {
  org: string;
  name: string;
}

export interface RegistrationTokenMintResponse {
  token: string;
  expires_at: number;
}

export const mintRegistrationToken = (
  body: RegistrationTokenMintRequest,
): Promise<RegistrationTokenMintResponse> =>
  request('/auth/registration-token', {
    method: 'POST',
    body,
  });

// ── Runtime-level registration token mint (THR-088 F-Step1) ──
// Machine-global mint: no org — the resulting executor profile is registered
// on the runtime (not scoped to an org). The daemon binds the minted `name`
// to that profile (`profile_name = record.name`), so the FE fixes the name up
// front and later polls GET /health/prereqs for it. Route is already shipped +
// classified (founder-only, loopback + master-bearer, like the org mint above).

export interface RuntimeRegistrationTokenMintRequest {
  name: string;
}

export const mintRuntimeRegistrationToken = (
  body: RuntimeRegistrationTokenMintRequest,
): Promise<RegistrationTokenMintResponse> =>
  request('/auth/registration-token/runtime', {
    method: 'POST',
    body,
  });

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
