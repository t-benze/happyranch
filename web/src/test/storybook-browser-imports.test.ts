/**
 * Browser import safety for the Storybook/provider seam.
 *
 * `PrototypeProvider` decorates the shipping `design-system/TasksList.stories.tsx`
 * stories, and `src/prototypes/index.tsx` mounts it in the built app. Every
 * module reachable from there is bundled for the browser, so a static `vitest`
 * import on that path crashes the real Storybook preview with "Vitest failed
 * to access its internal state" even though `composeStories` passes under
 * Vitest (TASK-8416 REVISE; TASK-8419 fixture repair). This guard fails closed
 * if a browser-shipped source module ever reintroduces a runner-only import.
 */
import { readFileSync, readdirSync } from 'node:fs';
import { dirname, join, relative, sep } from 'node:path';
import { fileURLToPath } from 'node:url';
import { describe, expect, test } from 'vitest';
import { mockSettingsApi } from '@/design-system/providers/_mock-settings';
import type { DaemonCapacityWrite, OrgSettingsPatch } from '@/lib/api/types';

const here = dirname(fileURLToPath(import.meta.url));
const webRoot = join(here, '../..');
const srcRoot = join(webRoot, 'src');
const testDir = join(srcRoot, 'test');

/** Test modules may import the runner; browser-shipped modules may not. */
function isTestModule(file: string): boolean {
  return file.startsWith(`${testDir}${sep}`) || /\.(test|spec)\.(ts|tsx)$/.test(file);
}

function sourceFiles(directory: string): string[] {
  return readdirSync(directory, { withFileTypes: true }).flatMap((entry) => {
    const path = join(directory, entry.name);
    if (entry.isDirectory()) return sourceFiles(path);
    return /\.(ts|tsx)$/.test(entry.name) ? [path] : [];
  });
}

const VITEST_IMPORT_PATTERNS = [
  /^\s*import\s+(?:type\s+)?[^\n]*from\s*['"]vitest['"]/m,
  /^\s*import\s*['"]vitest['"]/m,
  /\bimport\s*\(\s*['"]vitest['"]\s*\)/,
  /\brequire\s*\(\s*['"]vitest['"]\s*\)/,
];

function importsVitest(source: string): boolean {
  return VITEST_IMPORT_PATTERNS.some((pattern) => pattern.test(source));
}

describe('Storybook browser import safety', () => {
  test('every browser-shipped source module stays free of test-runner imports', () => {
    const offenders = sourceFiles(srcRoot)
      .filter((file) => !isTestModule(file))
      .filter((file) => importsVitest(readFileSync(file, 'utf8')))
      .map((file) => relative(webRoot, file))
      .sort();
    expect(offenders).toEqual([]);
  });

  test('the detector fails closed for each import form', () => {
    expect(importsVitest("import { vi } from 'vitest';")).toBe(true);
    expect(importsVitest("import { describe } from 'vitest'")).toBe(true);
    expect(importsVitest("import 'vitest';")).toBe(true);
    expect(importsVitest("const v = await import('vitest');")).toBe(true);
    expect(importsVitest("const v = require('vitest');")).toBe(true);
    expect(importsVitest("import { vi } from './vitest-shim';")).toBe(false);
    expect(importsVitest("// import { vi } from 'vitest' is a comment, not a module edge")).toBe(false);
  });

  test('the prototype Settings fixture preserves its contract without a test runner', async () => {
    const settings = mockSettingsApi.useSettings();
    expect(settings.isLoading).toBe(false);
    expect(settings.isError).toBe(false);
    expect(settings.data?.system.queue_workers.value).toBe(3);
    expect(settings.data?.org.reviewer_agents).toEqual(['code_reviewer']);

    const update = mockSettingsApi.useUpdateOrgSettings();
    expect(update.isPending).toBe(false);
    await expect(update.mutateAsync({} as OrgSettingsPatch)).resolves.toBe(settings.data);

    const capacity = mockSettingsApi.useDaemonCapacity();
    expect(capacity.data?.effective_admission_cap).toBe(13);
    // 19.6 — the capacity slot is widened with refresh/receipt/ordering
    // members, so the browser-safe mock must implement them too. These are
    // ADDITIVE: the value/mutation assertions above are unchanged and still
    // pass, because they inspect selected values, never the query shape.
    expect(typeof capacity.refetch).toBe('function');
    expect(typeof capacity.isFetching).toBe('boolean');
    expect(capacity.observation).not.toBeNull();
    expect(capacity.observation?.outcome).toBe('usable');
    await expect(capacity.refetch()).resolves.toBe(capacity.data);
    const capacityWrite = mockSettingsApi.useUpdateDaemonCapacity();
    await expect(capacityWrite.mutateAsync({} as DaemonCapacityWrite)).resolves.toEqual(capacity.data);

    expect(mockSettingsApi.useNextWakes('dev_agent').data?.next_wakes).toEqual([]);
  });
});
