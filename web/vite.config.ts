/// <reference types="vitest" />
import { defineConfig, loadEnv } from 'vite';
import react from '@vitejs/plugin-react';
import { createHash } from 'node:crypto';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { packagingCustody } from './scripts/packaging-custody';
import { packagingTailwind } from './scripts/packaging-tailwind';

// ------------------------------------------------------------ build-time UI feature flags
//
// THR-305. The SPA reads named booleans from ONE consumer module
// (`src/config/features.ts`), which imports `@happyranch/feature-flags`. This
// config resolves that specifier with `resolve.alias` only — no `define`, no
// injected env, no plugin and no config-time import — because the Mac custody
// validator pins those (app/mac/scripts/web_evidence.py). Profile data is read
// with fs, so it is not a config dependency.
//
// Selection is fixed per startup/build:
//   * `vite build` in every form              -> production (checked-in file only)
//   * `npm run build:local`                    -> local (marker + lifecycle, see
//                                                 scripts/build-local.mjs)
//   * `vite` serve (dev, Storybook dev)        -> local
//   * serve under Vitest                       -> test
//   * `vite preview`                           -> no resolution (serves dist)
// Production never reads the local file or the KB compatibility input.

export type FeatureProfile = 'production' | 'local' | 'test';
export type FeatureSelection = FeatureProfile | 'preview';
export type FeatureFlags = Record<string, boolean>;

export const FEATURE_FLAGS_MODULE = '@happyranch/feature-flags';
export const FEATURE_PROFILE_MARKER = Symbol.for('happyranch.featureProfile');
const FEATURE_LOCAL_LIFECYCLE = 'build:local';
const FEATURE_COMPAT_ENV = 'VITE_ENABLE_KB_COMPOSE';
const FEATURE_COMPAT_FLAG = 'kb_compose_ui';
const FEATURE_LOCAL_FILE = 'features.local.json';
const FEATURE_REGISTRY_META = ['purpose', 'owner', 'removal_condition'] as const;

function featureError(message: string): Error {
  return new Error(`[features] ${message}`);
}

function describeMarker(value: unknown): string {
  return typeof value === 'string' ? JSON.stringify(value) : typeof value;
}

/** Pick the flag profile for one Vite invocation; throws on any unsupported selector. */
export function selectFeatureProfile({
  command,
  isPreview = false,
  env = process.env,
  marker = (globalThis as Record<symbol, unknown>)[FEATURE_PROFILE_MARKER],
}: {
  command: 'build' | 'serve';
  isPreview?: boolean;
  env?: Record<string, string | undefined>;
  marker?: unknown;
}): FeatureSelection {
  const lifecycle = env.npm_lifecycle_event;
  if (marker !== undefined) {
    if (marker !== 'local' || command !== 'build' || lifecycle !== FEATURE_LOCAL_LIFECYCLE) {
      throw featureError(
        `unsupported selector: marker=${describeMarker(marker)} command=${command} lifecycle=${lifecycle ?? '(unset)'}; only \`npm run build:local\` may select the local profile for a build`,
      );
    }
    return 'local';
  }
  if (lifecycle === FEATURE_LOCAL_LIFECYCLE) {
    throw featureError('unsupported selector: lifecycle build:local without the build-local marker; run `npm run build:local`');
  }
  if (isPreview) return 'preview';
  if (command === 'build') return 'production';
  return env.VITEST ? 'test' : 'local';
}

function readFeatureJson(file: string, label: string): unknown {
  let raw: string;
  try {
    raw = fs.readFileSync(file, 'utf-8');
  } catch {
    throw featureError(`${label} ${file} is missing or unreadable`);
  }
  try {
    return JSON.parse(raw) as unknown;
  } catch {
    throw featureError(`${label} ${file} is not valid JSON`);
  }
}

function featureObject(value: unknown, file: string, label: string): Record<string, unknown> {
  if (typeof value !== 'object' || value === null || Array.isArray(value)) {
    throw featureError(`${label} ${file} root must be an object`);
  }
  return value as Record<string, unknown>;
}

function featureValues(
  value: unknown,
  file: string,
  label: string,
  names: string[],
  complete: boolean,
): FeatureFlags {
  const entries = featureObject(value, file, label);
  const out: FeatureFlags = {};
  for (const [key, flag] of Object.entries(entries)) {
    if (!names.includes(key)) throw featureError(`${label} ${file} has unknown flag "${key}"`);
    if (typeof flag !== 'boolean') throw featureError(`${label} ${file} flag "${key}" must be true or false (got ${flag === null ? 'null' : typeof flag})`);
    out[key] = flag;
  }
  if (complete) {
    for (const name of names) {
      if (!(name in out)) throw featureError(`${label} ${file} is missing flag "${name}"`);
    }
  }
  return out;
}

/**
 * Resolve the selected profile's flags, write the local/test snapshot and
 * return the alias target. The KB compatibility input comes from Vite's
 * `loadEnv(mode, webRoot)` (process env over `.env*` files).
 */
export function resolveFeatureFlags({
  profile,
  webRoot,
  mode,
}: {
  profile: FeatureProfile;
  webRoot: string;
  mode: string;
}): { flags: FeatureFlags; sources: Record<string, string>; ignored: string[]; aliasTarget: string; logLine: string } {
  const configDir = path.join(webRoot, 'config');
  const registryFile = path.join(configDir, 'features.registry.json');
  const registry = featureObject(readFeatureJson(registryFile, 'registry'), registryFile, 'registry');
  const names = Object.keys(registry);
  if (names.length === 0) throw featureError(`registry ${registryFile} declares no flags`);
  for (const name of names) {
    const entry = registry[name];
    if (typeof entry !== 'object' || entry === null || Array.isArray(entry)) {
      throw featureError(`registry ${registryFile} entry "${name}" must be an object`);
    }
    for (const field of FEATURE_REGISTRY_META) {
      const text = (entry as Record<string, unknown>)[field];
      if (typeof text !== 'string' || text.trim() === '') {
        throw featureError(`registry ${registryFile} entry "${name}" needs a nonempty ${field}`);
      }
    }
  }

  const localFile = path.join(configDir, FEATURE_LOCAL_FILE);
  const ignored: string[] = [];
  const sourceFiles: string[] = [];
  const sources: Record<string, string> = {};
  let flags: FeatureFlags;
  let aliasTarget: string;

  if (profile === 'production') {
    const file = path.join(configDir, 'features.production.json');
    flags = featureValues(readFeatureJson(file, 'production profile'), file, 'production profile', names, true);
    for (const name of names) sources[name] = 'production';
    sourceFiles.push('production');
    // Presence only: production never reads either input's value.
    if (fs.existsSync(localFile)) ignored.push(FEATURE_LOCAL_FILE);
    if (loadEnv(mode, webRoot, FEATURE_COMPAT_ENV)[FEATURE_COMPAT_ENV] !== undefined) ignored.push(FEATURE_COMPAT_ENV);
    aliasTarget = file;
  } else {
    const file = path.join(configDir, 'features.development.json');
    flags = featureValues(readFeatureJson(file, 'development profile'), file, 'development profile', names, true);
    for (const name of names) sources[name] = 'development';
    sourceFiles.push('development');
    if (fs.existsSync(localFile)) {
      if (profile === 'local') {
        const local = featureValues(readFeatureJson(localFile, 'local override'), localFile, 'local override', names, false);
        for (const [name, value] of Object.entries(local)) {
          flags[name] = value;
          sources[name] = FEATURE_LOCAL_FILE;
        }
        sourceFiles.push(FEATURE_LOCAL_FILE);
      } else {
        ignored.push(FEATURE_LOCAL_FILE);
      }
    }
    // Explicit KB compatibility input (local/test only), applied last:
    // literal 'true'/'false'; unset or empty means no opinion.
    const compat = loadEnv(mode, webRoot, FEATURE_COMPAT_ENV)[FEATURE_COMPAT_ENV];
    if (compat !== undefined && compat !== '') {
      if (compat !== 'true' && compat !== 'false') {
        throw featureError(`${FEATURE_COMPAT_ENV} must be the literal true or false (or empty/unset)`);
      }
      flags[FEATURE_COMPAT_FLAG] = compat === 'true';
      sources[FEATURE_COMPAT_FLAG] = FEATURE_COMPAT_ENV;
      sourceFiles.push(FEATURE_COMPAT_ENV);
    }
    const ordered = Object.fromEntries(names.map((name) => [name, flags[name]]));
    const content = `${JSON.stringify(ordered, null, 2)}\n`;
    const digest = createHash('sha256').update(content).digest('hex').slice(0, 8);
    const snapshotDir = path.join(webRoot, '.feature-flags');
    aliasTarget = path.join(snapshotDir, `resolved-${profile}-${digest}.json`);
    if (fs.existsSync(aliasTarget)) {
      if (fs.readFileSync(aliasTarget, 'utf-8') !== content) {
        throw featureError(`snapshot ${aliasTarget} does not match its content address; delete it and restart`);
      }
    } else {
      // Write-once, content-addressed: concurrent startups converge on one file.
      fs.mkdirSync(snapshotDir, { recursive: true });
      const temp = `${aliasTarget}.${process.pid}.${Date.now()}.tmp`;
      fs.writeFileSync(temp, content, { flag: 'wx' });
      fs.renameSync(temp, aliasTarget);
    }
  }

  const values = names.map((name) => `${name}=${flags[name]}(${sources[name]})`).join(' ');
  const logLine = `[features] profile=${profile} sources=${sourceFiles.join(',')} ${values}${ignored.length ? ` ignored: ${ignored.join(',')}` : ''}`;
  return { flags, sources, ignored, aliasTarget, logLine };
}

function resolveDaemonPort(): number {
  const home = process.env.HAPPYRANCH_DAEMON_HOME || path.join(os.homedir(), '.happyranch');
  const portFile = path.join(home, 'daemon.port');
  try {
    const raw = fs.readFileSync(portFile, 'utf-8').trim();
    const port = parseInt(raw, 10);
    if (!Number.isNaN(port) && port > 0) return port;
  } catch {
    /* fall through to default */
  }
  return 8765;
}

const EVIDENCE_ENV = process.env.I18N_BROWSER_EVIDENCE ?? '';
const EVIDENCE_NEGATIVE = EVIDENCE_ENV === 'negative';
const EVIDENCE_ENABLED = EVIDENCE_ENV === '1' || EVIDENCE_NEGATIVE;
const EVIDENCE_APP_IMPORT = "import { AppRoutes } from './routes';";
const EVIDENCE_APP_ANCHOR = '        <AppRoutes />';
const EVIDENCE_MAIN_MISMATCH_ANCHOR = 'const initialLocale = bootstrapDocumentLocale();';
const EVIDENCE_MAIN_PROVIDER_ANCHOR = '<App initialLocale={initialLocale} />';

/**
 * Evidence-only W2a shell instrumentation (THR-118). Independent of the W1
 * `I18N_BROWSER_EVIDENCE` flag, so W1 evidence builds stay byte-identical. It
 * is a NO-OP unless `I18N_W2A_EVIDENCE` is `1` or `negative`.
 *
 * `I18N_W2A_EVIDENCE=negative` additionally installs the passive correction
 * component and hands `main.tsx` a deliberately mismatched provider resolution
 * (while `bootstrapDocumentLocale()` still computes the document locale from
 * the saved preference). That makes the real shell commit the wrong language
 * first and repair itself afterwards, so the harness's frozen first-commit
 * predicate is proven causal.
 */
const W2A_EVIDENCE =
  process.env.I18N_W2A_EVIDENCE === '1' || process.env.I18N_W2A_EVIDENCE === 'negative';
const W2A_NEGATIVE = process.env.I18N_W2A_EVIDENCE === 'negative';
const W2A_ROUTES_OUTLET_ANCHOR = '            <Outlet />';

/**
 * Evidence-only build instrumentation for the THR-118 W1 browser harness.
 *
 * It is a NO-OP unless `I18N_BROWSER_EVIDENCE` is set. The default (`unset`),
 * every ordinary `npm run build`, `build-storybook`, Vitest run and CI build
 * therefore produce the shipping bytes untouched.
 *
 * When enabled it injects the test-only `I18nEvidenceConsumer` adjacent to
 * `<AppRoutes />` inside the REAL
 * `main.tsx -> App -> createBrowserRouter -> AppShell -> I18nProvider`
 * composition, so the harness can observe the first committed translated
 * consumer text through the production provider/router rather than a second
 * provider or a MemoryRouter. It never rewrites the resolver, the bootstrap
 * snapshot handoff, the provider or the router.
 *
 * `I18N_BROWSER_EVIDENCE=negative` (the isolated negative control only) also
 * hands the provider a deliberately mismatched `{locale:'en', source:'saved'}`
 * resolution in `main.tsx` — while the document locale from
 * `bootstrapDocumentLocale()` stays correct — and injects the passive-effect
 * correction component. That produces an English first commit under a correct
 * `zh-CN` `<html lang>` which a later effect repairs; it exists only to prove
 * the harness's first-commit assertion is causal.
 */
function i18nEvidenceInstrumentation() {
  return {
    name: 'i18n-browser-evidence-instrumentation',
    enforce: 'pre' as const,
    transform(code: string, id: string) {
      if (!EVIDENCE_ENABLED && !W2A_EVIDENCE) return null;
      const clean = id.split('?')[0].replace(/\\/g, '/');

      if (W2A_EVIDENCE && clean.endsWith('/src/App.tsx')) {
        if (!code.includes(EVIDENCE_APP_IMPORT) || !code.includes(EVIDENCE_APP_ANCHOR)) {
          throw new Error('w2a evidence: App.tsx instrumentation anchor not found');
        }
        const consumerImport = W2A_NEGATIVE
          ? "import { ShellEvidenceConsumer, ShellEvidenceCorrection } from './test/w2a-shell-evidence-consumer';"
          : "import { ShellEvidenceConsumer } from './test/w2a-shell-evidence-consumer';";
        const injected = W2A_NEGATIVE
          ? `${EVIDENCE_APP_ANCHOR}\n        <ShellEvidenceConsumer />\n        <ShellEvidenceCorrection />`
          : `${EVIDENCE_APP_ANCHOR}\n        <ShellEvidenceConsumer />`;
        return {
          code: code
            .replace(EVIDENCE_APP_IMPORT, `${EVIDENCE_APP_IMPORT}\n${consumerImport}`)
            .replace(EVIDENCE_APP_ANCHOR, injected),
          map: null,
        };
      }

      if (W2A_EVIDENCE && clean.endsWith('/src/routes.tsx')) {
        if (!code.includes(W2A_ROUTES_OUTLET_ANCHOR)) {
          throw new Error('w2a evidence: routes.tsx Outlet anchor not found');
        }
        return {
          code: code
            .replace(
              "import { AppBar } from '@/design-system/layouts/AppShell/AppBar';",
              "import { AppBar } from '@/design-system/layouts/AppShell/AppBar';\nimport { ShellErrorTrigger } from './test/w2a-shell-evidence-consumer';",
            )
            .replace(
              W2A_ROUTES_OUTLET_ANCHOR,
              `${W2A_ROUTES_OUTLET_ANCHOR}\n            <ShellErrorTrigger />`,
            ),
          map: null,
        };
      }

      // Negative control (W1 or W2a): hand the real provider a mismatched
      // resolution while `bootstrapDocumentLocale()` keeps the document locale
      // from the saved preference. Handled before the W1 gate because the W2a
      // negative is driven by `I18N_W2A_EVIDENCE=negative` alone.
      if ((EVIDENCE_NEGATIVE || W2A_NEGATIVE) && clean.endsWith('/src/main.tsx')) {
        if (
          !code.includes(EVIDENCE_MAIN_MISMATCH_ANCHOR) ||
          !code.includes(EVIDENCE_MAIN_PROVIDER_ANCHOR)
        ) {
          throw new Error('i18n evidence: main.tsx negative-control anchor not found');
        }
        return {
          code: code
            .replace(
              EVIDENCE_MAIN_MISMATCH_ANCHOR,
              `${EVIDENCE_MAIN_MISMATCH_ANCHOR}\n// NEGATIVE CONTROL ONLY: mismatched provider locale; the document locale above stays correct.\nconst evidenceMismatchedResolution = { locale: 'en' as const, source: 'saved' as const };`,
            )
            .replace(EVIDENCE_MAIN_PROVIDER_ANCHOR, '<App initialLocale={evidenceMismatchedResolution} />'),
          map: null,
        };
      }

      if (!EVIDENCE_ENABLED) return null;

      if (clean.endsWith('/src/App.tsx')) {
        if (!code.includes(EVIDENCE_APP_IMPORT) || !code.includes(EVIDENCE_APP_ANCHOR)) {
          throw new Error('i18n evidence: App.tsx instrumentation anchor not found');
        }
        const consumerImport = EVIDENCE_NEGATIVE
          ? "import { I18nEvidenceConsumer, I18nEvidenceCorrection, I18nEvidenceDocumentLocale } from './test/i18n-evidence-consumer';"
          : "import { I18nEvidenceConsumer } from './test/i18n-evidence-consumer';";
        const injected = EVIDENCE_NEGATIVE
          ? `${EVIDENCE_APP_ANCHOR}\n        <I18nEvidenceDocumentLocale />\n        <I18nEvidenceConsumer />\n        <I18nEvidenceCorrection />`
          : `${EVIDENCE_APP_ANCHOR}\n        <I18nEvidenceConsumer />`;
        return {
          code: code
            .replace(EVIDENCE_APP_IMPORT, `${EVIDENCE_APP_IMPORT}\n${consumerImport}`)
            .replace(EVIDENCE_APP_ANCHOR, injected),
          map: null,
        };
      }

      return null;
    },
  };
}

export default defineConfig(async ({ command, mode, isPreview }) => {
  const selection = selectFeatureProfile({ command, isPreview });
  let featureAlias: Record<string, string> = {};
  if (selection === 'preview') {
    console.info('[features] preview: serves existing dist');
  } else {
    const resolved = resolveFeatureFlags({ profile: selection, webRoot: __dirname, mode });
    console.info(resolved.logLine);
    featureAlias = { [FEATURE_FLAGS_MODULE]: resolved.aliasTarget };
  }
  const custody = packagingCustody();
  return {
    plugins: [i18nEvidenceInstrumentation(), react(), custody ? packagingTailwind(custody) : (await import('@tailwindcss/vite')).default(), ...(custody?.plugins() ?? [])],
    ...(custody ? { css: { postcss: { plugins: [] }, devSourcemap: false } } : {}),
    resolve: {
      alias: { ...featureAlias, '@': path.resolve(__dirname, 'src') },
    },
    server: {
      port: 5173,
      strictPort: true,
      proxy: {
        '/api': {
          target: `http://127.0.0.1:${resolveDaemonPort()}`,
          changeOrigin: false,
        },
      },
    },
    build: {
      outDir: 'dist',
      sourcemap: true,
    },
    test: {
      globals: true,
      environment: 'jsdom',
      setupFiles: ['./vitest.setup.ts'],
      css: false,
    },
  };
});
