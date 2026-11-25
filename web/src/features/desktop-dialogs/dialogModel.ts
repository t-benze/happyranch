/**
 * Pure model for the desktop dialog host (THR-387 S6-W1; handoff v2.4 §2-§3).
 *
 * Desktop Main shows at most ONE top-level web dialog: Settings or
 * Connection. Opening either while the other is open replaces it in the same
 * document; nothing here spawns a window. This module has no React or DOM
 * imports so its rules can be checked by a plain Node test.
 */

/**
 * Settings dialog pages, in nav order (v2.4 §2: This app, This computer,
 * then Runtime). `local` (HappyRanch on this computer, S7-L2) is a
 * desktop-only page with no browser Settings route. `task-attributes`
 * (THR-390 PR6) follows Organization and is OPTIONAL: it is a page only when
 * the caller passes it in `enabledOptional` (the build-time flag
 * `task_attributes_ui`; this module stays import-free for its Node test).
 */
export const DESKTOP_SETTINGS_SECTIONS = [
  'preferences',
  'local',
  'daemon-capacity',
  'executors',
  'organization',
  'task-attributes',
] as const;

export type DesktopSettingsSection = (typeof DESKTOP_SETTINGS_SECTIONS)[number];

/** Pages that exist only when enabled by the caller (build-time UI flags). */
export const OPTIONAL_SETTINGS_SECTIONS: readonly DesktopSettingsSection[] = ['task-attributes'];

/** Page used when a request names no page or an unknown/legacy page. */
export const DEFAULT_ORG_SETTINGS_SECTION: DesktopSettingsSection = 'daemon-capacity';
export const DEFAULT_RECOVERY_SETTINGS_SECTION: DesktopSettingsSection = 'preferences';

export type DesktopDialogRequest =
  | {
      dialog: 'settings';
      section?: DesktopSettingsSection;
      /** Page for a fresh Settings open when `section` is absent. */
      fallbackSection?: DesktopSettingsSection;
    }
  | { dialog: 'connection' };

export type DesktopDialogState =
  | { dialog: null }
  | { dialog: 'settings'; section: DesktopSettingsSection }
  | { dialog: 'connection' };

export const CLOSED: DesktopDialogState = { dialog: null };

function isSection(value: string, enabledOptional: readonly DesktopSettingsSection[]): value is DesktopSettingsSection {
  if (!(DESKTOP_SETTINGS_SECTIONS as readonly string[]).includes(value)) return false;
  const section = value as DesktopSettingsSection;
  return !OPTIONAL_SETTINGS_SECTIONS.includes(section) || enabledOptional.includes(section);
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
  return { dialog: 'settings', section: request.fallbackSection ?? DEFAULT_ORG_SETTINGS_SECTION };
}

/**
 * Map a Main Settings route to the exact dialog page it names.
 *
 * `/orgs/:slug/settings/<page>` selects that page. The page route's own
 * redirects are mirrored: no page, the legacy `assistant`/`system`/`agents`
 * pages and unknown pages select Capacity. The org-less recovery route
 * `/settings/<page>` defaults to Language (Preferences), as its page does.
 * An optional page not in `enabledOptional` is unknown (it selects the
 * default page). Returns null for any other path.
 */
export function settingsRequestFromPath(
  pathname: string,
  enabledOptional: readonly DesktopSettingsSection[] = [],
): DesktopDialogRequest | null {
  const org = /^\/orgs\/[^/]+\/settings(?:\/([^/]*))?(?:\/|$)/.exec(pathname);
  if (org) {
    const page = org[1] ?? '';
    return { dialog: 'settings', section: isSection(page, enabledOptional) ? page : DEFAULT_ORG_SETTINGS_SECTION };
  }
  const recovery = /^\/settings(?:\/([^/]*))?(?:\/|$)/.exec(pathname);
  if (recovery) {
    const page = recovery[1] ?? '';
    return { dialog: 'settings', section: isSection(page, enabledOptional) ? page : DEFAULT_RECOVERY_SETTINGS_SECTION };
  }
  return null;
}

/** True for a Main Settings route (org or org-less recovery). */
export function isSettingsPath(pathname: string): boolean {
  return settingsRequestFromPath(pathname) !== null;
}

/**
 * Validate a native `app.dialogRequested` event (protocol
 * `native-connection-bridge.v2.json#/$defs/dialogRequested`; bridge spec
 * "v2 dialog request event"). The data is a JSON string that must be exactly
 * `{"schema":2,"dialog":"settings"|"connection"}`. Anything else (malformed
 * JSON, a non-object, missing/extra keys, another schema or dialog) is
 * invalid and returns null: the caller ignores the whole event.
 */
export function parseDialogRequested(data: unknown): 'settings' | 'connection' | null {
  if (typeof data !== 'string') return null;
  let value: unknown;
  try {
    value = JSON.parse(data);
  } catch {
    return null;
  }
  if (!value || typeof value !== 'object' || Array.isArray(value)) return null;
  const keys = Object.keys(value);
  if (keys.length !== 2 || !keys.includes('schema') || !keys.includes('dialog')) return null;
  const { schema, dialog } = value as { schema: unknown; dialog: unknown };
  if (schema !== 2) return null;
  return dialog === 'settings' || dialog === 'connection' ? dialog : null;
}

/**
 * The Settings request for a native entry (no page named): keep the page
 * already shown, otherwise Capacity when Main shows an organization and
 * Language when it does not (as the org-less Settings route does).
 */
export function nativeDialogRequest(
  dialog: 'settings' | 'connection',
  mainPathname: string,
): DesktopDialogRequest {
  if (dialog === 'connection') return { dialog: 'connection' };
  const inOrg = /^\/orgs\/[^/]+(?:\/|$)/.test(mainPathname);
  return {
    dialog: 'settings',
    fallbackSection: inOrg ? DEFAULT_ORG_SETTINGS_SECTION : DEFAULT_RECOVERY_SETTINGS_SECTION,
  };
}

/**
 * The in-app path a link inside a dialog leads to, or null when the dialog
 * must not handle it: another document/origin, a new tab or window, a
 * download, a modified or non-primary click, or a same-page hash link.
 */
export function dialogLinkTarget(
  link: { href: string; target: string; hasDownload: boolean },
  click: { button: number; modified: boolean },
  current: { protocol: string; host: string; pathname: string; search: string },
): string | null {
  if (click.button !== 0 || click.modified || link.hasDownload) return null;
  if (link.target && link.target !== '_self') return null;
  let url: URL;
  try {
    url = new URL(link.href);
  } catch {
    return null;
  }
  if (url.protocol !== current.protocol || url.host !== current.host) return null;
  if (url.pathname === current.pathname && url.search === current.search && url.hash) return null;
  return `${url.pathname}${url.search}${url.hash}`;
}
