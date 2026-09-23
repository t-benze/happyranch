/**
 * W2b onboarding evidence-contract guard (THR-118).
 *
 * The W2b browser harness deliberately adds NO evidence-only bundle
 * instrumentation: it drives the ordinary production bundle and switches locale
 * through the supported browser preference. This test is the machine-readable
 * proof of that design, so a future edit cannot quietly (a) add a W2b evidence
 * gate/consumer to the shipping app graph, or (b) introduce a public language
 * selector to make the harness simpler.
 */
import { readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { describe, expect, it } from 'vitest';
import { LOCALE_STORAGE_KEY } from '@/lib/i18n';

const here = dirname(fileURLToPath(import.meta.url));
const webRoot = join(here, '../..');

function read(relativePath: string): string {
  return readFileSync(join(webRoot, relativePath), 'utf8');
}

describe('W2b onboarding browser-evidence contract', () => {
  it('installs no W2b evidence instrumentation in the app graph or Vite config', () => {
    const config = read('vite.config.ts');
    expect(config).not.toContain('I18N_W2B_EVIDENCE');
    expect(config).not.toContain('w2b-onboarding-evidence-consumer');
    // The real composition never references W2b evidence hooks.
    expect(read('src/App.tsx')).not.toMatch(/w2b/i);
    expect(read('src/routes.tsx')).not.toMatch(/w2b/i);
  });

  it('uses the supported browser preference path, not a shipping selector', () => {
    const harness = read('scripts/w2b-onboarding-browser-evidence.mjs');
    expect(harness).toContain('new StorageEvent(');
    expect(harness).toContain(LOCALE_STORAGE_KEY);
    expect(harness).toContain('/onboarding');
    // The harness must not drive a locale toggle control from the app.
    expect(harness).not.toContain('test-set-locale');
    expect(harness).not.toContain('w2a-set-');
  });
});
