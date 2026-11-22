/**
 * Per-request remote read-only refusal state (THR-387 S8-2b).
 *
 * Native answers a configuration change on a remote Host with an exact
 * `403 {"detail":{"code":"remote_read_only"}}` before forwarding it. That
 * answer can reach the page BEFORE native's `app.stateChanged` reports the
 * remote target, so the refusal is recorded from the response itself, never
 * from app status alone. Pure module: no React, no I/O.
 */
import type { AppState } from '../native/appStatus';
import type { OwnerStatus } from '../owners/ownerRegistry';
import { isRemoteReadOnlyError } from './policy';

export interface RemoteRefusal {
  /**
   * The remote Host name native reported when the refusal arrived; '' when it
   * did not name one yet (the UI then shows the live Host name, or its
   * localized "the Host computer" fallback).
   */
  host: string;
  /**
   * True when the refused request carried no unsent draft (a removal,
   * retire/restore, a toggle): the refusal is shown on its own, not as an
   * unsaved draft.
   */
  draftless: boolean;
}

/** The refusal recorded for `error`, or null when it is not native's 403 remote_read_only. */
export function remoteRefusalFor(error: unknown, app: AppState | null, draftless: boolean): RemoteRefusal | null {
  if (!isRemoteReadOnlyError(error)) return null;
  const target = app?.target;
  return { host: target?.kind === 'remote' ? target.name : '', draftless };
}

/**
 * The refusal a config owner shows for its latest request. A draft refusal
 * shows while that draft is still the failed one; a draftless refusal shows
 * while the owner has nothing unsent or outstanding (its registry status is
 * clean/confirmed, never `failed`). Pending/unknown/dirty hide it.
 */
export function shownRemoteRefusal(status: OwnerStatus | undefined, refusal: RemoteRefusal | null): RemoteRefusal | null {
  if (refusal === null || status === undefined) return null;
  if (status === 'failed') return refusal;
  if (refusal.draftless && (status === 'clean' || status === 'confirmed')) return refusal;
  return null;
}
