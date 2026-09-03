import { request } from './client';
import type { SettingsSnapshot, OrgSettingsPatch, NextWakesResponse, DaemonCapacitySnapshot, DaemonCapacityWrite } from './types';

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
// Machine-global mint for built-in binary registration or custom-adapter
// submission. Purpose is mandatory so retired profile registration cannot be
// selected through omission.

export interface RuntimeRegistrationTokenMintRequest {
  name: string;
  /** Explicit registration flow; legacy profile registration is retired. */
  purpose: 'binary' | 'adapter';
  /** For 'adapter' purpose: the profile name this adapter is bound to. */
  intended_profile_name?: string;
  /** Optional Slice-1A direct-authority workspace adapter for adapter mints. */
  workspace_adapter_id?: 'claude' | 'codex' | 'opencode' | 'pi';
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

export const getDaemonCapacity = (slug: string): Promise<DaemonCapacitySnapshot> =>
  request(`/orgs/${slug}/settings/daemon-capacity`);

export const putDaemonCapacity = (
  slug: string,
  body: DaemonCapacityWrite,
): Promise<DaemonCapacitySnapshot> => {
  const { revision, ...payload } = body;
  return request(`/orgs/${slug}/settings/daemon-capacity`, {
    method: 'PUT',
    body: payload,
    headers: { 'If-Match': `"${revision}"` },
  });
};

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
