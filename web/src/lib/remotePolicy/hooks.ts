/**
 * React accessors for the remote client configuration policy (THR-387 S8-2b).
 *
 * The active target comes from native `app.status` only (desktop). Browser
 * mode, This computer, and a desktop whose status is not yet known are not
 * remote: their controls keep their existing behavior.
 */
import { useNativeAppStatus } from '../native/hooks';
import { isRemoteConfigOperation, type RemoteConfigOperation } from './policy';

/** The committed remote Host's display name, or null when not remote. */
export function useRemoteHost(): string | null {
  const app = useNativeAppStatus();
  return app?.target?.kind === 'remote' ? app.target.name : null;
}

/**
 * The remote Host's name when ANY of `operations` is a policy `config` entry
 * and the active target is remote (the control is read-only: "Change this on
 * {Host}"); null otherwise. Operations are exact policy templates, e.g.
 * `'PUT /api/v1/orgs/{slug}/settings/org'`, so the UI follows the same file
 * native enforces.
 */
export function useRemoteReadOnly(...operations: RemoteConfigOperation[]): string | null {
  const host = useRemoteHost();
  if (host === null) return null;
  return operations.some(isRemoteConfigOperation) ? host : null;
}
