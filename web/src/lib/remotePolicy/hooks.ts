/**
 * React accessors for the remote client configuration policy (THR-387 S8-2b).
 *
 * The active target comes from native `app.status` only (desktop). Browser
 * mode, This computer, and a desktop whose status is not yet known are not
 * remote: their controls keep their existing behavior.
 */
import { useCallback, useState } from 'react';
import { useNativeAppStatus } from '../native/hooks';
import { getAppStatus } from '../native/target';
import { isRemoteConfigOperation, type RemoteConfigOperation } from './policy';
import { remoteRefusalFor, type RemoteRefusal } from './refusal';

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

/**
 * Per-request remote read-only refusal for an ownerless form (Create
 * organization, Add agent). `note(error)` records native's `403
 * remote_read_only` from the response itself (true when it was one), so the
 * refusal shows before app status reports the remote target; `clear()` on
 * the next edit or submit. `host` is the Host to name (live, else captured,
 * else ''), or null when there is no refusal.
 */
export function useRemoteRefusal(): {
  host: string | null;
  note: (error: unknown) => boolean;
  clear: () => void;
} {
  const live = useRemoteHost();
  const [refusal, setRefusal] = useState<RemoteRefusal | null>(null);
  const note = useCallback((error: unknown) => {
    const next = remoteRefusalFor(error, getAppStatus(), false);
    if (next) setRefusal(next);
    return next !== null;
  }, []);
  const clear = useCallback(() => setRefusal(null), []);
  return { host: refusal === null ? null : live ?? refusal.host, note, clear };
}
