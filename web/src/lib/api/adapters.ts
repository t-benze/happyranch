/** Custom adapter API client (THR-107 seq141).

GET  /api/v1/runtime/adapters         - list all adapters
GET  /api/v1/runtime/adapters/{id}   - poll adapter status
POST /api/v1/runtime/adapters/{id}/bind-profile  - bind approved adapter to profile
 */
import { request } from './client';

/** Server-authoritative recovery eligibility (THR-107 TASK-3784).
 *  The browser MUST NOT recompute hash/tamper eligibility — this field
 *  is the single source of truth derived from durable server state.
 *
 *  - 'ready_to_bind': approved + hash-valid + profile absent → show Bind
 *  - 'already_bound': profile exists bound to this adapter → show Connected
 *  - 'cross_profile': profile exists bound to DIFFERENT adapter → no Bind/Connected
 *  - 'builtin_collision': intended profile name is a built-in → no Bind
 *  - 'tampered': on-disk hash mismatch / missing → no Bind
 *  - 'pending': adapter is PENDING → not recoverable
 *  - 'not_intended': no intended_profile_name → not recoverable
 *  - null: not approved / unknown → not recoverable */
export type AdapterEligibility =
  | 'ready_to_bind'
  | 'already_bound'
  | 'cross_profile'
  | 'builtin_collision'
  | 'tampered'
  | 'pending'
  | 'not_intended'
  | null;

export interface AdapterEntry {
  id: string;
  name: string;
  executable: string;
  executable_hash: string;
  version: string;
  capabilities: string[];
  contract_version: number;
  workspace_adapter: string;
  status: string;
  registered_at: string;
  registered_by: string;
  approved_at: string | null;
  approved_by: string | null;
  intended_profile_name: string | null;
  /** Server-authoritative eligibility — browser MUST NOT recompute. */
  eligibility: AdapterEligibility;
}

export interface AdapterListResponse {
  adapters: AdapterEntry[];
}

export interface BindProfileRequest {
  profile_name: string;
}

export interface BindProfileResponse {
  profile_name: string;
  command_adapter_id: string;
  workspace_adapter_id: string;
  kind: string;
  status: string;
  adapter_id: string;
}

/** List all registered custom adapters (bearer-authenticated management endpoint). */
export const listAdapters = (): Promise<AdapterEntry[]> =>
  request('/runtime/adapters');

export const getAdapter = (adapterId: string): Promise<AdapterEntry> =>
  request(`/runtime/adapters/${adapterId}`);

export const bindAdapterProfile = (
  adapterId: string,
  body: BindProfileRequest,
): Promise<BindProfileResponse> =>
  request(`/runtime/adapters/${adapterId}/bind-profile`, {
    method: 'POST',
    body,
  });
