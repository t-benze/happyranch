#!/usr/bin/env node
/**
 * `npm run build:local` — an ordinary production-mode bundle that uses the
 * LOCAL feature-flag profile (development defaults + optional
 * `config/features.local.json` + explicit `VITE_ENABLE_KB_COMPOSE`).
 *
 * It sets the build-local marker and calls Vite's `build()` in this process,
 * so `vite.config.ts` can see both the marker and `npm_lifecycle_event ===
 * 'build:local'`. Every other build (`npm run build`, release callers, a direct
 * `vite build`) has no marker and selects the production profile.
 *
 * Supported arguments: `--outDir <dir>`, `--sourcemap`.
 */
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const webRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const args = process.argv.slice(2);
const build = {};
for (let i = 0; i < args.length; i += 1) {
  const value = args[i];
  if (value === '--outDir' && i + 1 < args.length) build.outDir = path.resolve(args[++i]);
  else if (value === '--sourcemap') build.sourcemap = true;
  else {
    console.error(`build:local: unsupported argument ${JSON.stringify(value)} (supported: --outDir <dir>, --sourcemap)`);
    process.exit(2);
  }
}

globalThis[Symbol.for('happyranch.featureProfile')] = 'local';
const { build: viteBuild } = await import('vite');
try {
  await viteBuild({ configFile: path.join(webRoot, 'vite.config.ts'), root: webRoot, build });
} catch (error) {
  console.error(error instanceof Error ? error.message : String(error));
  process.exit(1);
}
