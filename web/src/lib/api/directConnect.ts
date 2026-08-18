/** Direct custom-CLI connect API client (THR-107 slice 3).

GET  /api/v1/runtime/custom-cli/status                      - wrapper destination + operation/projection state, by profile name
POST /api/v1/runtime/custom-cli/{operation_id}/commit        - project a received receipt to a durable Connected profile
POST /api/v1/runtime/custom-cli/{operation_id}/retry         - revalidate an immutable failed receipt snapshot
POST /api/v1/runtime/custom-cli/{operation_id}/forget        - clear a terminal failed receipt only

Note: POST /api/v1/runtime/custom-cli/connect is intentionally NOT bound
here — it is called by the candidate CLI itself (via the copy-pasted
curl script), authenticated with the scoped registration token, never by
the browser. See tests/contract/route-classification.json.
 */
import { request } from './client';

export type ProfileState = 'planned' | 'committed' | 'failed' | null;

export interface DirectConnectStatus {
  wrapper_destination: string;
  operation_id: string | null;
  profile_state: ProfileState;
  reason: string | null;
  /** Present only when a retry established a live profile; the original
   * projection remains immutable failed evidence. */
  historical_projection_state?: 'failed';
  historical_projection_reason?: string | null;
  retry_state?: 'succeeded';
}

export function getStatus(intendedProfileName: string): Promise<DirectConnectStatus> {
  return request<DirectConnectStatus>('/runtime/custom-cli/status', {
    params: { intended_profile_name: intendedProfileName },
  });
}

export interface CommitResponse {
  operation_id: string;
  profile_state: ProfileState;
  profile_name?: string;
  reason?: string;
}

export function commit(operationId: string): Promise<CommitResponse> {
  return request<CommitResponse>(`/runtime/custom-cli/${operationId}/commit`, {
    method: 'POST',
  });
}

export function retry(operationId: string): Promise<CommitResponse> {
  return request<CommitResponse>(`/runtime/custom-cli/${operationId}/retry`, {
    method: 'POST',
  });
}

export type ForgetWrapperStatus = 'removed' | 'already_absent' | 'preserved_changed' | 'preserved_unsafe';

export interface ForgetResponse {
  operation_id: string;
  status: 'forgotten';
  wrapper_status: ForgetWrapperStatus;
}

/** Clear only a server-authorized terminal failed direct-connect operation. */
export function forget(operationId: string): Promise<ForgetResponse> {
  return request<ForgetResponse>(`/runtime/custom-cli/${operationId}/forget`, {
    method: 'POST',
  });
}
