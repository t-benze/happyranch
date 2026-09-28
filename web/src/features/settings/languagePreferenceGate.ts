/**
 * THR-118 W2c — closed production gate for the Settings ▸ Preferences
 * language selector.
 *
 * W3 (not W2c) is authorized to expose the opt-in Chinese preview publicly, so
 * the selector ships built and tested but INACTIVE: the Preferences sub-nav
 * entry and the `preferences` route are only mounted when the build sets
 * `VITE_ENABLE_I18N_PREFERENCES=true`. Ordinary `npm run build` / CI / desktop
 * builds never set it, so a direct `/orgs/:slug/settings/preferences` URL falls
 * through to the existing settings catch-all redirect (Assistant).
 *
 * The flag follows the existing `VITE_ENABLE_PROTOTYPES` /
 * `VITE_ENABLE_KB_COMPOSE` build-time pattern. Vitest flips it per test with
 * `vi.stubEnv`; the browser evidence harness builds with it explicitly. W3
 * removes this gate when the preview is accepted.
 */
export function isLanguagePreferenceEnabled(): boolean {
  return import.meta.env.VITE_ENABLE_I18N_PREFERENCES === 'true';
}
