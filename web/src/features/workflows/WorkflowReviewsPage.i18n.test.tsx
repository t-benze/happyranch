import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { expect, it } from 'vitest';
import { COVERAGE_MANIFEST, sourceSurfaceIdentity, type SourceInventory } from '@/lib/i18n/coverage';
import { en } from '@/lib/i18n/locales/en';
import { zhCN } from '@/lib/i18n/locales/zh-CN';

it('U7 owns both real routes and mounted dialogs with matching bilingual catalog parameters', async () => {
  const webRoot = join(dirname(fileURLToPath(import.meta.url)), '../../..');
  const { inventorySource } = await import(join(webRoot, 'scripts/i18n-source-inventory.mjs')) as { inventorySource(root: string): SourceInventory };
  const inventory = inventorySource(webRoot);
  const entry = COVERAGE_MANIFEST.find(e => e.namespace === 'workflows')!;
  expect(entry.status).toBe('translated');
  const actualRoutes = inventory.routes.filter(r => r.token.startsWith('reviews')).map(r => `${r.path}#${r.parents?.join('/') || 'root'}:${r.token}`);
  expect(actualRoutes).toEqual(entry.qualifiedRouteTokens);
  const mounted = inventory.dialogs.filter(d => d.path.includes('/workflows/')).map(sourceSurfaceIdentity);
  expect(mounted).toEqual(entry.qualifiedSurfaces);
  for (const key of Object.keys(en).filter(k => k.startsWith('workflows.') || k.endsWith('.reviews')) as (keyof typeof en)[]) {
    expect(typeof zhCN[key]).toBe('string');
    expect([...String(en[key]).matchAll(/\{(\w+)\}/g)].map(m => m[1]).sort()).toEqual([...String(zhCN[key]).matchAll(/\{(\w+)\}/g)].map(m => m[1]).sort());
  }
});
