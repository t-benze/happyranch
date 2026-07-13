/**
 * Mirror of runtime-level executor routes from routes/executors.py (THR-088).
 *
 * These are CLI-facing, loopback-only, scoped-token-gated routes. They are
 * NOT called from the SPA directly — they are consumed by the onboarding
 * flow in ConnectRuntimeStep where the user copies a prompt to their CLI.
 *
 * Routes:
 *   POST /api/v1/executors/runtime/register-binary  — register a binary path
 */
import { request } from './client';

/** Request to register a binary path via scoped token.
 *
 * The kind is determined from the registration token's scope — there is NO
 * ``kind`` field in the body. This guarantees a token scoped to ``claude``
 * can only write the ``claude`` binary path.
 */
export interface RegisterBinaryScopedRequest {
  /** Absolute path to the executor binary. */
  path: string;
}

/** Response after successfully registering a binary path. */
export interface RegisterBinaryScopedResponse {
  /** Executor kind (from the token scope). */
  kind: string;
  /** Resolved absolute path. */
  path: string;
  /** Always true on success (validated before storage). */
  valid: boolean;
}

/** Register a binary path for a built-in executor kind via the scoped-token
 *  loopback flow. The caller must supply a binary-purpose registration token
 *  in the Authorization header.
 *
 *  Throws ApiError on:
 *  - 401: invalid/expired/consumed token, or wrong scope
 *  - 403: token purpose is not 'binary' (profile tokens rejected)
 *  - 400: conformance challenge incomplete
 *  - 422: path validation failure (non-absolute / missing / not executable)
 */
export const registerBinaryScoped = (
  token: string,
  body: RegisterBinaryScopedRequest,
): Promise<RegisterBinaryScopedResponse> =>
  request('/executors/runtime/register-binary', {
    method: 'POST',
    body,
    headers: { Authorization: `Bearer ${token}` },
  });
