import { readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { describe, expect, it } from 'vitest';
import {
  classifyRouteToken,
  COVERAGE_MANIFEST,
  coverageSummary,
  describeCoverage,
  extractRouteTokens,
  unclassifiedRouteTokens,
} from './coverage';

const here = dirname(fileURLToPath(import.meta.url));

function source(relativePath: string): string {
  return readFileSync(join(here, relativePath), 'utf8');
}

const ROUTE_SOURCES = [
  '../../routes.tsx',
  '../../prototypes/index.tsx',
  '../../features/settings/SettingsPage.tsx',
];

describe('coverage manifest (W1 acceptance case 7)', () => {
  it('classifies every mounted route token in the route modules', () => {
    const tokens = ROUTE_SOURCES.flatMap((path) => extractRouteTokens(source(path)));
    expect(tokens.length).toBeGreaterThan(0);
    expect(unclassifiedRouteTokens(tokens)).toEqual([]);
  });

  it('classifies settings subroutes and redirects', () => {
    for (const token of ['settings/*', 'assistant', 'daemon-capacity', 'organization', 'executors', 'system', 'spend', 'schedule']) {
      expect(classifyRouteToken(token), `unclassified token ${token}`).toBeDefined();
    }
  });

  it('catches a newly mounted unclassified route', () => {
    // The check's failure signal: a synthetic new route token is reported.
    expect(unclassifiedRouteTokens(['brand-new-unmapped-surface'])).toEqual([
      'brand-new-unmapped-surface',
    ]);
    expect(classifyRouteToken('brand-new-unmapped-surface')).toBeUndefined();
  });

  it('leaves all not-yet-migrated surfaces marked incomplete (no route-wide Chinese claim)', () => {
    const summary = coverageSummary();
    expect(summary.translated).toBe(0);
    expect(summary.englishOnly).toBeGreaterThan(0);
    for (const entry of COVERAGE_MANIFEST) {
      expect(['translated', 'english-only', 'not-applicable']).toContain(entry.status);
      if (entry.namespace !== 'prototypes' && entry.namespace !== 'redirects') {
        expect(entry.status, `namespace ${entry.namespace}`).toBe('english-only');
      }
    }
  });

  it('renders the visible English marker for incomplete surfaces', () => {
    expect(describeCoverage('en', 'english-only')).toBe('English only — not yet migrated');
    expect(describeCoverage('zh-CN', 'english-only')).toBe('仅英文 — 尚未迁移');
    expect(describeCoverage('en', 'not-applicable')).toBe('No user-facing copy');
  });

  it('records reachable shared dialogs in the manifest surfaces', () => {
    const surfaceText = COVERAGE_MANIFEST.flatMap((entry) => entry.surfaces).join(' | ');
    for (const dialog of [
      'NewThreadDialog',
      'ArchiveDialog',
      'TaskCancelDialog',
      'JobReviewDialog',
      'SettingsDialog',
    ]) {
      expect(surfaceText).toContain(dialog);
    }
  });
});
