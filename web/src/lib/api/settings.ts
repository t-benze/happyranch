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
  manager: string | null;
  manager_kind?: 'agent' | 'human';
  human_manager?: 'founder' | null;
  is_default?: boolean;
  workers: string[];
}

export const putTeams = (
  slug: string,
  patch: TeamsPatchBody,
): Promise<{ teams: TeamRow[] }> =>
  request(`/orgs/${slug}/settings/teams`, { method: 'PUT', body: patch });

import { getToken } from '../auth';
import type { CodingSettingsCommand, CodingSettingsView, CodingSettingsSave } from './types';

/** These closed grammars have ASCII field names and integer numeric values.
 * Construct a recursively sorted body for the existing JSON.stringify client;
 * do not send a JSON string (which that client would quote a second time).
 * Refuse unsafe JS integers and ill-formed Unicode rather than change bytes.
 */
function canonicalCodingBody(value: unknown): unknown {
  if (typeof value === 'number') {
    if (!Number.isSafeInteger(value) || Object.is(value, -0)) throw new TypeError('coding integer cannot be represented exactly');
    return value;
  }
  if (typeof value === 'string') {
    // UTF-8 cannot represent unpaired UTF-16 surrogate code units.
    if (/[\uD800-\uDBFF](?![\uDC00-\uDFFF])|(?<![\uD800-\uDBFF])[\uDC00-\uDFFF]/u.test(value)) {
      throw new TypeError('coding text must be valid Unicode');
    }
    return value;
  }
  if (value === null || typeof value === 'boolean') return value;
  if (Array.isArray(value)) return value.map(canonicalCodingBody);
  if (typeof value !== 'object' || Object.getPrototypeOf(value) !== Object.prototype) {
    throw new TypeError('coding command must contain plain JSON values');
  }
  return Object.fromEntries(Object.keys(value).sort().map(key => [
    key, canonicalCodingBody((value as Record<string, unknown>)[key]),
  ]));
}

/** Read-only reconciliation: keep the original key/body after lost transport. */
export const getCodingSettings = async (
  slug: string,
  operationKey?: string,
): Promise<CodingSettingsView> => {
  const result = await request<CodingSettingsView>(`/orgs/${slug}/settings/coding`, {
    params: { operation_key: operationKey },
  });
  canonicalCodingBody(result); // Refuse an unrepresentable numeric readback.
  return result;
};

export const putCodingSettings = async (
  slug: string,
  command: CodingSettingsCommand,
): Promise<CodingSettingsSave> => {
  if (slug !== command.org_slug) throw new TypeError('coding command org mismatch');
  const body = canonicalCodingBody(command);
  if (new TextEncoder().encode(JSON.stringify(body)).length > 65536) {
    throw new TypeError('coding command exceeds 65536 UTF-8 bytes');
  }
  // The existing explicit-token option sends once, including on 401. This
  // consumes the ordinary Founder bearer without changing shared transport or
  // token custody. No automatic write retry/rebootstrap/reapply is requested.
  const result = await request<CodingSettingsSave>(`/orgs/${slug}/settings/coding`, {
    method: 'PUT', body, auth: { token: await getToken() },
  });
  canonicalCodingBody(result);
  return result;
};
