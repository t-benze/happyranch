/**
 * Mirror of runtime/daemon/routes/executor_binaries.py (THR-085).
 *
 * Machine-local executor binary-path registry — the WRITE surface a human
 * operator uses to tell the daemon WHERE each executor CLI binary lives on
 * THIS host. Three bearer-authed, daemon-GLOBAL routes (NOT org-scoped):
 *   - GET  /api/v1/executor-binaries           — list registered kinds
 *   - POST /api/v1/executor-binaries/register  — set a kind's absolute path
 *   - POST /api/v1/executor-binaries/validate   — check a path without storing
 *
 * Discovery is REGISTRATION-ONLY (founder ruling THR-085 msg45): the user
 * supplies the path via manual entry; the daemon validates + stores. There is
 * NO detect/scan route — do not add one.
 *
 * Honesty fence: the shapes below mirror the EXACT server pydantic models.
 * No invented fields.
 */
import { request } from './client';

/** The four built-in executor kinds. The list route returns only kinds that
 *  have a stored path, so the client infers the never-registered ones from
 *  the absence of an entry against this known set. */
export const EXECUTOR_BINARY_KINDS = ['claude', 'codex', 'pi', 'opencode'] as const;
export type ExecutorBinaryKind = (typeof EXECUTOR_BINARY_KINDS)[number];

/** A single executor kind's entry in the registry. */
export interface BinaryRegistryEntry {
  kind: string;
  /** Absolute path to the binary, or null if not registered. */
  path: string | null;
  /** True when the stored path exists and is executable. */
  valid: boolean;
}

/** Full machine-local registry listing. */
export interface BinaryRegistryList {
  entries: BinaryRegistryEntry[];
}

/** Request to register or update a binary path for an executor kind. */
export interface RegisterBinaryRequest {
  kind: string;
  /** Absolute path to the binary. */
  path: string;
}

/** Response after successfully registering a binary path. */
export interface RegisterBinaryResponse {
  kind: string;
  path: string;
  valid: boolean;
}

/** Request to validate a binary path without storing it. */
export interface ValidateBinaryRequest {
  /** Absolute path to check. */
  path: string;
}

/** Response after path validation. `valid` is false with a human-readable
 *  `error` when the path is not absolute / missing / not executable. */
export interface ValidateBinaryResponse {
  path: string;
  valid: boolean;
  error: string | null;
}

/** List all executor kinds with a stored path plus their current validity. */
export const listExecutorBinaries = (): Promise<BinaryRegistryList> =>
  request('/executor-binaries');

/** Register (or update) the absolute binary path for an executor kind. The
 *  daemon validates BEFORE storing — a bad path throws ApiError (422). */
export const registerExecutorBinary = (
  body: RegisterBinaryRequest,
): Promise<RegisterBinaryResponse> =>
  request('/executor-binaries/register', { method: 'POST', body });

/** Validate a path (absolute + exists + executable) WITHOUT storing it. Never
 *  throws for a bad path — returns `{ valid: false, error }` instead. */
export const validateExecutorBinary = (
  body: ValidateBinaryRequest,
): Promise<ValidateBinaryResponse> =>
  request('/executor-binaries/validate', { method: 'POST', body });
