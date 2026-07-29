/**
 * Mirror of runtime-level executor routes from routes/executors.py (THR-088).
 *
 * Two auth postures live in this file:
 *   - register-binary is CLI-facing, loopback-only, scoped-token-gated. It is
 *     NOT called from the SPA directly — it is consumed by the onboarding
 *     flow in ConnectRuntimeStep where the user copies a prompt to their CLI.
 *   - profiles LIST/REMOVE are founder-facing MANAGEMENT routes on the
 *     STANDARD session bearer (same posture as /executor-binaries) and ARE
 *     SPA-callable — the Settings custom-profiles view consumes them
 *     (THR-107 S4).
 *
 * Routes:
 *   POST   /api/v1/executors/runtime/register-binary — register a binary path
 *   GET    /api/v1/executors/runtime/profiles        — list custom profiles
 *   DELETE /api/v1/executors/runtime/profiles/{name} — remove a custom profile
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
  /** Preserves the validated caller-supplied spelling — not canonicalized. */
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
    auth: { token },
  });

/** Summary of one custom executor profile in the machine-global runtime
 *  store. Mirrors the EXACT server pydantic model — no invented fields. */
export interface RuntimeProfileEntry {
  /** Profile name (runtime store key). */
  name: string;
  /** Executable name from the stored profile definition, or null. */
  command: string | null;
  /** D6 canonical: workspace adapter id (claude/codex/opencode/pi), or null. */
  workspace_adapter_id: string | null;
  /** D6 canonical: command adapter id. May be 'generic-cli' (template-based generic CLI)
   *  or 'custom-adapter:<id>' (bound to a separately registered, founder-approved,
   *  hash-verified custom adapter executable — D7B, subprocess-only, mandatory v1
   *  AdapterInput/AdapterOutput, D5 baseline-only posture). */
  command_adapter_id: string | null;
  /** @deprecated Use workspace_adapter_id instead. Preserved for backward compat. */
  adapter: string | null;
  /** @deprecated Alias for workspace_adapter_id. Same meaning as adapter. */
  adapter_id: string | null;
  /** @deprecated Use command_adapter_id instead. Preserved for backward compat. */
  command_adapter: string | null;
  /** True when the profile has a valid entry in the machine-local
   *  binary registry (executors.json) keyed by the profile name — the
   *  same gating as built-ins (THR-107 seq155). No PATH-based fallback. */
  present: boolean;
  /** The resolved absolute path when present, else null. */
  path: string | null;
  /** D7A: result-envelope enforcement posture.
   *  'strict' = mandatory v1 envelope enforcement;
   *  null = legacy compatibility (optional envelope). */
  envelope_policy: string | null;
}

/** All custom profiles in the machine-global runtime store. */
export interface RuntimeProfileList {
  profiles: RuntimeProfileEntry[];
}

/** Response after removing a custom executor profile. */
export interface RemoveRuntimeProfileResponse {
  name: string;
  removed: boolean;
}

/** List custom executor profiles from the machine-global runtime store.
 *  Standard session bearer — NOT the scoped-token flow above. */
export const listRuntimeProfiles = (): Promise<RuntimeProfileList> =>
  request('/executors/runtime/profiles');

/** Remove a custom executor profile (durable store + in-memory registry).
 *  Standard session bearer — NOT the scoped-token flow above.
 *
 *  Throws ApiError on:
 *  - 404: no custom profile with that name in the runtime store
 *  - 422: name collides with a built-in executor (never removable)
 */
export const removeRuntimeProfile = (
  name: string,
): Promise<RemoveRuntimeProfileResponse> =>
  request(`/executors/runtime/profiles/${encodeURIComponent(name)}`, {
    method: 'DELETE',
  });
