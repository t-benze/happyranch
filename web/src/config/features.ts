/**
 * Build-time UI feature flags (THR-305) — the ONLY module the SPA reads flags
 * from. Feature code imports `features` from `@/config/features`; it never
 * reads `import.meta.env` or the JSON profiles itself.
 *
 * `@happyranch/feature-flags` is resolved by `vite.config.ts` (resolve.alias)
 * to the checked-in production profile for every `vite build`, or to a
 * resolved local/test snapshot for dev, `npm run build:local` and Vitest. The
 * registry in `web/config/features.registry.json` names each flag with its
 * purpose, owner and removal condition. Values are fixed for one startup or
 * build: restart the dev server or rebuild to change them. This is UI
 * visibility only; it is not an authorization or backend control.
 */
import resolved from '@happyranch/feature-flags';

type Registry = typeof import('../../config/features.registry.json');

/** Every registered flag name. */
export type FeatureFlagName = keyof Registry;

export type FeatureFlags = Readonly<Record<FeatureFlagName, boolean>>;

export const features: FeatureFlags = Object.freeze({ ...resolved });
