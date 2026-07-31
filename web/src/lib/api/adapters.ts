/** Custom adapter API client (THR-107 seq141).

GET  /api/v1/runtime/adapters         - list all adapters
GET  /api/v1/runtime/adapters/{id}   - poll adapter status
POST /api/v1/runtime/adapters/{id}/bind-profile  - bind approved adapter to profile
 */
import { request } from './client';

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
