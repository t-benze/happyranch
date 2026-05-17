/**
 * Contract test: every component file under `design-system/{primitives,
 * patterns,layouts}/` ships a `meta` export AND has an entry in the
 * committed `registry.json`. Mirrors `openapi-coverage.test.ts` for the
 * daemon-route surface.
 *
 * The test fails when:
 *   - a new component is added without a `meta` block (registry-blind);
 *   - a component file's `meta.name` doesn't appear in `registry.json`
 *     (the committed copy is stale — run `npm run build:registry`);
 *   - `registry.json` lists a component whose source file is gone or
 *     no longer exports a matching `meta` (orphan entry).
 *
 * This is the static counterpart to the CI freshness check in
 * `scripts/verify-design-system.sh`.
 */
import { readFileSync, readdirSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join, resolve } from 'node:path';
import { describe, expect, test } from 'vitest';

const __filename = fileURLToPath(import.meta.url);
const __dirname = dirname(__filename);
const WEB_ROOT = resolve(__dirname, '../..');
const DS_ROOT = join(WEB_ROOT, 'src/design-system');
const REGISTRY_PATH = join(DS_ROOT, 'registry.json');
const SUBDIRS = ['primitives', 'patterns', 'layouts'] as const;
// Files that live under those subdirs but are NOT components themselves —
// the design route page is a composition, not a registry entry.
const SKIP = new Set<string>([join(DS_ROOT, '__design__/index.tsx')]);

interface RegistryEntry {
  name: string;
  layer: 'primitive' | 'pattern' | 'layout';
  import: string;
}
interface Registry {
  version: number;
  components: RegistryEntry[];
}

function walkTsx(dir: string): string[] {
  const out: string[] = [];
  for (const entry of readdirSync(dir, { withFileTypes: true })) {
    const path = join(dir, entry.name);
    if (entry.isDirectory()) {
      out.push(...walkTsx(path));
    } else if (entry.isFile() && entry.name.endsWith('.tsx') && !SKIP.has(path)) {
      out.push(path);
    }
  }
  return out;
}

function extractMetaName(src: string): string | null {
  const match = src.match(
    /export const meta\s*=\s*\{[\s\S]*?name:\s*["']([^"']+)["']/,
  );
  return match ? match[1] : null;
}

describe('design-system registry coverage', () => {
  const componentFiles = SUBDIRS.flatMap((sub) => walkTsx(join(DS_ROOT, sub)));
  const registry = JSON.parse(readFileSync(REGISTRY_PATH, 'utf-8')) as Registry;
  const registryNames = new Set(registry.components.map((c) => c.name));

  test('every component file exports a meta block', () => {
    const missing: string[] = [];
    for (const file of componentFiles) {
      const src = readFileSync(file, 'utf-8');
      if (!/^export const meta\s*=/m.test(src)) {
        missing.push(file.slice(WEB_ROOT.length + 1));
      }
    }
    expect(missing).toEqual([]);
  });

  test('every meta export has a matching entry in registry.json', () => {
    const filesNotInRegistry: string[] = [];
    for (const file of componentFiles) {
      const src = readFileSync(file, 'utf-8');
      const name = extractMetaName(src);
      if (name && !registryNames.has(name)) {
        filesNotInRegistry.push(`${file.slice(WEB_ROOT.length + 1)} → ${name}`);
      }
    }
    expect(filesNotInRegistry).toEqual([]);
  });

  test('registry.json has no orphan entries', () => {
    const fileNames = new Set<string>();
    for (const file of componentFiles) {
      const src = readFileSync(file, 'utf-8');
      const name = extractMetaName(src);
      if (name) fileNames.add(name);
    }
    const orphans = [...registryNames].filter((n) => !fileNames.has(n));
    expect(orphans).toEqual([]);
  });

  test('registry.json layer assignment matches the source directory', () => {
    const mismatches: string[] = [];
    for (const entry of registry.components) {
      const expectedSub = `${entry.layer}s`;
      if (!entry.import.includes(`/${expectedSub}/`)) {
        mismatches.push(`${entry.name}: layer=${entry.layer} but import=${entry.import}`);
      }
    }
    expect(mismatches).toEqual([]);
  });
});
