/**
 * Pure transition rules for the contextual owner gate (THR-387 S6-W2a).
 * No React/DOM: checked by `web/scripts/owner-registry.test.mjs`.
 */
import type { OwnerRecord } from './ownerRegistry';

/**
 * What the requested transition does, named in the Discard action. `quit` and
 * `relaunch` (with `close`) are native lifecycle requests (S6-W2b).
 */
export type OwnerGateEffect = 'close' | 'openConnection' | 'changePage' | 'changeOrg' | 'leave' | 'quit' | 'relaunch';

interface PathLike {
  pathname: string;
  search: string;
}

const ORG_PATH = /^\/orgs\/([^/]+)(?:\/|$)/;

/** Org slug a Main path shows, or null. */
export function orgOfPath(pathname: string): string | null {
  return ORG_PATH.exec(pathname)?.[1] ?? null;
}

/**
 * Owners a Main navigation affects: Main owners when the page (path or query)
 * changes; Settings owners only when the org changes (the Settings dialog
 * follows Main's org).
 */
export function navigationTransition(
  current: PathLike,
  next: PathLike,
): { effect: OwnerGateEffect; affects: (owner: OwnerRecord) => boolean } {
  const orgChange = orgOfPath(current.pathname) !== orgOfPath(next.pathname);
  const pageChange = current.pathname !== next.pathname || current.search !== next.search;
  return {
    effect: orgChange ? 'changeOrg' : 'leave',
    affects: (owner) => (owner.scope === 'main' ? pageChange : orgChange),
  };
}
