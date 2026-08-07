/** Scoped custom-CLI direct-connect API. The browser carries only the
 * short-lived registration token; it never receives a wrapper destination. */
import { request } from './client';

export interface DirectConnectDependency {
  slot: string;
  executable: string;
  version_probe_argv: string[];
}

export interface DirectConnectRequest {
  version: string;
  capabilities?: string[];
  dependency_manifest_version: 2;
  dependencies: DirectConnectDependency[];
}

export interface DirectConnectResponse {
  adapter_id: string;
  state: 'pre_projection';
  wrapper_sha256: string;
}

export const connectDirectCustomCli = (
  token: string,
  body: DirectConnectRequest,
): Promise<DirectConnectResponse> =>
  request('/runtime/custom-cli/connect', {
    method: 'POST',
    auth: { token },
    body,
  });
