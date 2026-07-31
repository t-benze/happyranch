/** Custom adapter API client (THR-107 seq141 + removal + seq220).

GET    /api/v1/runtime/adapters                     - list all adapters
GET    /api/v1/runtime/adapters/{id}                - poll adapter status
POST   /api/v1/runtime/adapters/{id}/approve        - approve pending adapter (seq220)
POST   /api/v1/runtime/adapters/{id}/reject          - reject/remove pending adapter (seq220)
POST   /api/v1/runtime/adapters/{id}/bind-profile    - bind approved adapter to profile
DELETE /api/v1/runtime/adapters/{id}                 - remove an approved adapter (THR-107)
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
 *  - 'recovery_ready': no intended_profile_name + hash-valid → advanced Bind recovery
 *  - null: not approved / unknown → not recoverable */
export type AdapterEligibility =
  | 'ready_to_bind'
  | 'already_bound'
  | 'cross_profile'
  | 'builtin_collision'
  | 'tampered'
  | 'pending'
  | 'recovery_ready'
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
  /** THR-107 seq244: dependency manifest version (null for legacy). */
  dependency_manifest_version: number | null;
  /** THR-107 seq244: declared child executable dependencies. */
  dependencies: Array<{ executable: string; sha256: string }>;
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

/** Request body for adapter removal — every material identity/binding fact
 *  MUST match the server's durable snapshot exactly.
 *  THR-107 seq244 fix-forward: includes dependency manifest facts. */
export interface RemoveAdapterRequest {
  executable: string;
  executable_hash: string;
  version: string;
  capabilities: string[];
  contract_version: number;
  workspace_adapter: string;
  name: string;
  intended_profile_name: string | null;
  /** THR-107 seq244: dependency manifest version (null for legacy). */
  dependency_manifest_version: number | null;
  /** THR-107 seq244: declared child executable dependencies (null/empty for legacy). */
  dependencies: Array<{ executable: string; sha256: string }> | null;
}

export interface RemoveAdapterResponse {
  id: string;
  removed: boolean;
  name: string;
}

/** Request body for adapter approval — 6 material identity facts
 *  plus optional dependency manifest facts.
 *  Every field MUST match the server's durable snapshot exactly.
 *  THR-107 seq244 fix-forward: includes dependency manifest facts. */
export interface ApproveAdapterRequest {
  executable: string;
  executable_hash: string;
  version: string;
  capabilities: string[];
  contract_version: number;
  workspace_adapter: string;
  /** THR-107 seq244: dependency manifest version (null for legacy). */
  dependency_manifest_version: number | null;
  /** THR-107 seq244: declared child executable dependencies (null/empty for legacy). */
  dependencies: Array<{ executable: string; sha256: string }> | null;
}

/** Response from adapter approval (THR-107 seq237).
 *  Includes all AdapterEntry fields plus optional profile_binding info
 *  when the adapter had an intended_profile_name and was auto-bound. */
export interface ApproveAdapterResponse extends AdapterEntry {
  /** Present when the adapter was auto-bound to its intended profile.
   *  Contains {profile_name, command_adapter_id, workspace_adapter_id,
   *  kind, status, adapter_id}. Absent for no-intended adapters or when
   *  the profile was already bound (idempotent retry). */
  profile_bound?: BindProfileResponse | null;
}

/** Request body for adapter rejection — same 6 material identity facts
 *  as approval plus optional dependency manifest facts.
 *  Every field MUST match the server's durable snapshot.
 *  Rejects stale, re-registered, and hash-changed snapshots.
 *  THR-107 seq244 fix-forward: includes dependency manifest facts. */
export interface RejectAdapterRequest {
  executable: string;
  executable_hash: string;
  version: string;
  capabilities: string[];
  contract_version: number;
  workspace_adapter: string;
  /** THR-107 seq244: dependency manifest version (null for legacy). */
  dependency_manifest_version: number | null;
  /** THR-107 seq244: declared child executable dependencies (null/empty for legacy). */
  dependencies: Array<{ executable: string; sha256: string }> | null;
}

export interface RejectAdapterResponse {
  id: string;
  rejected: boolean;
  name: string;
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

/** Remove an APPROVED custom adapter (THR-107 founder-gated destructive action).
 *  The caller MUST supply an exact snapshot of all material identity/binding
 *  facts — the server rejects stale, re-registered, and wrong-target snapshots.
 *
 *  Throws ApiError on:
 *  - 401: missing/invalid bearer token
 *  - 404: adapter not found
 *  - 422: snapshot mismatch, not APPROVED, or profile-referenced
 */
export const removeAdapter = (
  adapterId: string,
  body: RemoveAdapterRequest,
): Promise<RemoveAdapterResponse> =>
  request(`/runtime/adapters/${adapterId}`, {
    method: 'DELETE',
    body,
  });

/** Approve a PENDING custom adapter (THR-107 seq237 founder-gated).
 *  The caller MUST supply the exact 6 material identity facts of the
 *  PENDING durable snapshot — the server rejects stale/mismatched snapshots.
 *
 *  **THR-107 seq237**: When the adapter has an intended_profile_name, the
 *  server atomically approves AND creates/binds the named custom profile in
 *  one transaction. The response includes ``profile_bound`` with the binding
 *  result. No client-side bind follow-up is needed — a refetch shows
 *  eligibility='already_bound' and Connected.
 *
 *  Adapters without intended_profile_name retain explicit advanced Bind.
 *
 *  Throws ApiError on:
 *  - 401: missing/invalid bearer token
 *  - 404: adapter not found
 *  - 422: snapshot mismatch, not PENDING, already approved with different
 *         facts, or profile binding failure (rolled back to PENDING)
 */
export const approveAdapter = (
  adapterId: string,
  body: ApproveAdapterRequest,
): Promise<ApproveAdapterResponse> =>
  request(`/runtime/adapters/${adapterId}/approve`, {
    method: 'POST',
    body,
  });

/** Reject/remove a PENDING custom adapter (THR-107 seq220 founder-gated).
 *  The caller MUST supply the exact 6 material identity facts of the
 *  PENDING durable snapshot — the server rejects stale/mismatched snapshots.
 *  No persisted rejected status; atomically removes the PENDING entry.
 *
 *  Throws ApiError on:
 *  - 401: missing/invalid bearer token
 *  - 404: adapter not found
 *  - 422: snapshot mismatch or not PENDING
 */
export const rejectAdapter = (
  adapterId: string,
  body: RejectAdapterRequest,
): Promise<RejectAdapterResponse> =>
  request(`/runtime/adapters/${adapterId}/reject`, {
    method: 'POST',
    body,
  });
