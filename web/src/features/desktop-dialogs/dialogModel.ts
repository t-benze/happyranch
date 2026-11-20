/**
 * Pure model for the desktop dialog host (THR-387 S6-W1; handoff v2.4 §2-§3).
 *
 * Desktop Main shows at most ONE top-level web dialog: Settings or
 * Connection. Opening either while the other is open replaces it in the same
 * document; nothing here spawns a window. This module has no React or DOM
 * imports so its rules can be checked by a plain Node test.
 */

/** Settings dialog pages, in nav order (v2.4 §2: This app, then Runtime). */
export const DESKTOP_SETTINGS_SECTIONS = [
  'preferences',
  'daemon-capacity',
  'executors',
  'organization',
  'coding',
] as const;

export type DesktopSettingsSection = (typeof DESKTOP_SETTINGS_SECTIONS)[number];

/** Page used when a request names no page or an unknown/legacy page. */
export const DEFAULT_ORG_SETTINGS_SECTION: DesktopSettingsSection = 'daemon-capacity';
export const DEFAULT_RECOVERY_SETTINGS_SECTION: DesktopSettingsSection = 'preferences';

export type DesktopDialogRequest =
  | { dialog: 'settings'; section?: DesktopSettingsSection }
  | { dialog: 'connection' };

export type DesktopDialogState =
  | { dialog: null }
  | { dialog: 'settings'; section: DesktopSettingsSection }
  | { dialog: 'connection' };

export const CLOSED: DesktopDialogState = { dialog: null };

function isSection(value: string): value is DesktopSettingsSection {
  return (DESKTOP_SETTINGS_SECTIONS as readonly string[]).includes(value);
}

/**
 * The dialog state after an open request. Repeated entry selects the
 * existing dialog/page; a Settings request without a page keeps the page that
 * is already shown, or falls back to the default page.
 */
export function applyOpenRequest(
  current: DesktopDialogState,
  request: DesktopDialogRequest,
): DesktopDialogState {
  if (request.dialog === 'connection') return { dialog: 'connection' };
  if (request.section) return { dialog: 'settings', section: request.section };
  if (current.dialog === 'settings') return current;
  return { dialog: 'settings', section: DEFAULT_ORG_SETTINGS_SECTION };
}

/**
 * Map a Main Settings route to the exact dialog page it names.
 *
 * `/orgs/:slug/settings/<page>` selects that page. The page route's own
 * redirects are mirrored: no page, the legacy `assistant`/`system`/`agents`
 * pages and unknown pages select Capacity. The org-less recovery route
 * `/settings/<page>` defaults to Language (Preferences), as its page does.
 * Returns null for any other path.
 */
export function settingsRequestFromPath(pathname: string): DesktopDialogRequest | null {
  const org = /^\/orgs\/[^/]+\/settings(?:\/([^/]*))?(?:\/|$)/.exec(pathname);
  if (org) {
    const page = org[1] ?? '';
    return { dialog: 'settings', section: isSection(page) ? page : DEFAULT_ORG_SETTINGS_SECTION };
  }
  const recovery = /^\/settings(?:\/([^/]*))?(?:\/|$)/.exec(pathname);
  if (recovery) {
    const page = recovery[1] ?? '';
    return { dialog: 'settings', section: isSection(page) ? page : DEFAULT_RECOVERY_SETTINGS_SECTION };
  }
  return null;
}

/** True for a Main Settings route (org or org-less recovery). */
export function isSettingsPath(pathname: string): boolean {
  return settingsRequestFromPath(pathname) !== null;
}
