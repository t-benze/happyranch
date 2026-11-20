/**
 * Is this document the desktop app's Main (native mode), not a browser tab?
 *
 * THR-387 S6-W1 assumption, until the S5-W1 native status store lands: the
 * desktop app serves the packaged web app from the `happyranch-app:` scheme
 * (the same check `lib/api/multipart.ts` uses), or the native shell installs
 * the `call`/`on` bridge facade on `window.happyranchConnection`. A browser
 * tab served by the daemon has neither, so browser mode is unchanged.
 * Presentation only: this grants no bridge capability or authority.
 */
export function isDesktopShell(): boolean {
  if (typeof window === 'undefined') return false;
  if (window.location.protocol === 'happyranch-app:') return true;
  const facade = (window as { happyranchConnection?: { call?: unknown; on?: unknown } })
    .happyranchConnection;
  return !!facade && typeof facade.call === 'function' && typeof facade.on === 'function';
}
