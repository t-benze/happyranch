import { copyFileSync, mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { describe, expect, it } from 'vitest';
import { ESLint } from 'eslint';
import {
  classifyRouteIdentity,
  classifySourceRoute,
  fullReleaseIssues,
  sourceSurfaceIdentity,
  type SourceInventory,
  type NamespaceCoverage,
  classifyRouteToken,
  COVERAGE_MANIFEST,
  coverageSummary,
  describeCoverage,
  NOT_APPLICABLE_NAMESPACES,
  unclassifiedRouteIdentities,
  unclassifiedRouteTokens,
} from './coverage';

const here = dirname(fileURLToPath(import.meta.url));
const webRoot = join(here, '../../..');

function read(relativePath: string): string {
  return readFileSync(join(webRoot, relativePath), 'utf8');
}

const scriptPath = join(webRoot, 'scripts/i18n-source-inventory.mjs');
const { inventorySource } = await import(scriptPath) as { inventorySource(root: string): SourceInventory };
function scannedInventory(): SourceInventory { return inventorySource(webRoot); }
function scannedIdentities(): string[] {
  return scannedInventory().routes.map(({ path, token }) => path === 'src/features/settings/SettingsPage.tsx' ? `SettingsPage.tsx:${token}` : token);
}

function surfacesFor(namespace: string): readonly string[] {
  const entry = COVERAGE_MANIFEST.find((candidate) => candidate.namespace === namespace);
  if (!entry) throw new Error(`manifest is missing namespace ${namespace}`);
  return entry.surfaces;
}

function namespaceStatus(namespace: string): string | undefined {
  return COVERAGE_MANIFEST.find((entry) => entry.namespace === namespace)?.status;
}

/** Real mounted definitions: unused imports/declarations never qualify. */
function definedDialogs(): Set<string> {
  return new Set(scannedInventory().dialogs.map(site => site.symbol));
}

describe('coverage manifest (W1 acceptance case 7)', () => {
  it('classifies every route token in the real route modules', () => {
    const bareTokens = scannedInventory().routes.filter(route => route.path === 'src/routes.tsx').map(route => route.token);
    expect(bareTokens.length).toBeGreaterThan(0);
    expect(unclassifiedRouteTokens(bareTokens)).toEqual([]);
  });

  it('classifies every qualified identity for colliding tokens', () => {
    const identities = scannedIdentities();
    expect(identities).toContain('SettingsPage.tsx:*');
    expect(identities).toContain('SettingsPage.tsx:index');
    expect(unclassifiedRouteIdentities(identities)).toEqual([]);
  });

  it('classifies the copy-bearing catch-all/root shell as translated (W2a)', () => {
    // W2a migrated `*` (NotFound message + Go home link) and `index`
    // (RootRedirect loading copy) into the catalog. The route modules now
    // reference the shell keys instead of holding English literals.
    expect(classifyRouteToken('*')?.status).toBe('translated');
    expect(classifyRouteToken('index')?.status).toBe('translated');
    expect(classifyRouteIdentity('routes.tsx:*')?.status).toBe('translated');
    const routes = read('src/routes.tsx');
    expect(routes).toContain('shell.notFound.body');
    expect(routes).toContain('shell.loading');
    const en = read('src/lib/i18n/locales/en.ts');
    expect(en).toContain("'shell.notFound.body': 'Not found. {link}.'");
    expect(en).toContain("'shell.loading': 'Loading…'");
  });

  it('classifies the true redirects as not-applicable via bare and qualified identities', () => {
    for (const token of ['/orgs/:slug', 'spend', 'schedule']) {
      expect(classifyRouteToken(token)?.status, token).toBe('not-applicable');
    }
    for (const identity of [
      'SettingsPage.tsx:index',
      'SettingsPage.tsx:system',
      'SettingsPage.tsx:agents',
      'SettingsPage.tsx:*',
      'system',
    ]) {
      expect(classifyRouteIdentity(identity)?.status, identity).toBe('not-applicable');
    }
  });

  it('classifies the W2c Settings copy-bearing subroutes (incl. gated preferences) translated', () => {
    for (const token of [
      'settings/*',
      'assistant',
      'daemon-capacity',
      'organization',
      'executors',
      'preferences',
    ]) {
      expect(classifyRouteToken(token)?.status, token).toBe('translated');
    }
    expect(scannedIdentities()).toContain('SettingsPage.tsx:preferences');
    expect(surfacesFor('settings')).toContain('PreferencesSection');
  });

  it('catches a newly mounted unclassified route', () => {
    expect(unclassifiedRouteTokens(['brand-new-unmapped-surface'])).toEqual([
      'brand-new-unmapped-surface',
    ]);
    expect(classifyRouteToken('brand-new-unmapped-surface')).toBeUndefined();
    expect(unclassifiedRouteIdentities(['brand-new-unmapped-surface'])).toEqual([
      'brand-new-unmapped-surface',
    ]);
  });

  it('marks the migrated route namespaces and proven mounted Assistant translated without changing other classifications', () => {
    const summary = coverageSummary();
    expect(summary.translated).toBeGreaterThanOrEqual(21);
    expect(summary.notApplicable).toBeGreaterThanOrEqual(3);
    expect(summary.total).toBe(summary.translated + summary.englishOnly + summary.notApplicable);
    const translated = COVERAGE_MANIFEST.filter((entry) => entry.status === 'translated')
      .map((entry) => entry.namespace)
      .sort();
    expect(translated).toEqual(expect.arrayContaining([
      'agents',
      'app-shell',
      'artifacts',
      'audit',
      'dashboard',
      'dreams',
      'health',
      'help-and-palette',
      'jobs',
      'kb',
      'not-found',
      'onboarding',
      'root-shell',
      'settings',
      'skills',
      'system-assistant',
      'tasks',
      'threads',
      'todos',
      'usage',
      'work-hours',
    ]));
    expect(namespaceStatus('system-assistant')).toBe('translated');
    for (const entry of COVERAGE_MANIFEST) {
      expect(['translated', 'english-only', 'not-applicable']).toContain(entry.status);
      if (NOT_APPLICABLE_NAMESPACES.includes(entry.namespace)) {
        expect(entry.status, `namespace ${entry.namespace}`).toBe('not-applicable');
      }
    }
  });

  it('renders the visible English marker for incomplete surfaces', () => {
    expect(describeCoverage('en', 'english-only')).toBe('English only — not yet migrated');
    expect(describeCoverage('zh-CN', 'english-only')).toBe('仅英文 — 尚未迁移');
    expect(describeCoverage('en', 'not-applicable')).toBe('No user-facing copy');
  });

  it('anchors every manifest dialog surface to a real mounted consumer', () => {
    expect(fullReleaseIssues(scannedInventory())).toEqual([]);
  });

  it('anchors the Assistant dock and conversation switcher to actual mounted consumers', () => {
    expect(read('src/routes.tsx')).toMatch(/<AssistantDockHost\s*\/>/);
    expect(read('src/features/system-assistant/AssistantDockHost.tsx')).toMatch(/<ConversationSwitcher\b/);
    expect(surfacesFor('system-assistant')).toEqual(['AssistantDockHost', 'ConversationSwitcher']);
  });

  it('contains no invented dialog/overlay names', () => {
    const defined = definedDialogs();
    const dialogSurfaces = COVERAGE_MANIFEST.flatMap((entry) => entry.surfaces).filter((surface) =>
      /^[A-Za-z0-9]+Dialog$/.test(surface),
    );
    expect(dialogSurfaces.length).toBeGreaterThan(0);
    for (const surface of dialogSurfaces) {
      expect(defined.has(surface), `manifest names undefined dialog ${surface}`).toBe(true);
    }
    // Reviewer regression: these names were fabricated in the W1 head.
    const surfaceText = COVERAGE_MANIFEST.flatMap((entry) => entry.surfaces).join(' | ');
    for (const invented of [
      'TaskCancelDialog',
      'TaskRevisitDialog',
      'JobReviewDialog',
      'AbandonDialog',
      'ThreadDetailPane',
      'SettingsDialog',
    ]) {
      expect(surfaceText, `invented name ${invented} must not return`).not.toContain(invented);
    }
  });

  it('keeps every not-applicable namespace copy-free in the manifest data', () => {
    for (const namespace of NOT_APPLICABLE_NAMESPACES) {
      expect(namespaceStatus(namespace)).toBe('not-applicable');
    }
  });
});

/** Disk fixtures use the final shipping script/config and the worktree's locked
 * tools via normal parent resolution. No node_modules symlink or mock scanner.
 */
async function diskFixture(action: (root: string, put: (file: string, body: string) => void) => void | Promise<void>): Promise<void> {
  const root = mkdtempSync(join(webRoot, '.i18n-source-fixture-'));
  const put = (file: string, body: string): void => {
    mkdirSync(dirname(join(root, file)), { recursive: true });
    writeFileSync(join(root, file), body);
  };
  try {
    for (const file of ['index.html', 'tsconfig.json', 'vite.config.ts', 'eslint.config.js', 'package.json', 'scripts/i18n-source-inventory.mjs']) {
      mkdirSync(dirname(join(root, file)), { recursive: true });
      copyFileSync(join(webRoot, file), join(root, file));
    }
    put('src/main.tsx', "import { App } from './App'; import { createRoot } from 'react-dom/client'; createRoot(document.getElementById('root')!).render(<App />);");
    put('src/App.tsx', "export function App() { return <p>{label}</p>; }");
    await action(root, put);
  } finally { rmSync(root, { recursive: true }); }
}

describe('C5-C7 actual disk source discovery and full-release boundary', () => {
  it('C5 final copied real config rejects disk copy and malformed/overbroad exact exceptions', () => diskFixture(async (root, put) => {
    put('src/App.tsx', 'export const App = () => <h1>New disk heading</h1>;');
    const eslint = new ESLint({ cwd: root, allowInlineConfig: false });
    const [result] = await eslint.lintFiles(['src/App.tsx']);
    expect(result.messages.filter(message => message.ruleId === 'owned-copy/no-untranslated-copy')).toEqual([
      expect.objectContaining({ message: expect.stringContaining('New disk heading') }),
    ]);
    const script = readFileSync(join(root, 'scripts/i18n-source-inventory.mjs'), 'utf8');
    put('scripts/i18n-source-inventory.mjs', script.replace("raw('design-system/layouts/AppShell/Sidebar.tsx'", "raw('design-system/layouts/AppShell/*.tsx'"));
    const path = join(root, 'scripts/i18n-source-inventory.mjs');
    const { auditCopyExceptions } = await import(path);
    expect(() => auditCopyExceptions(inventorySource(root))).toThrow(/exact path\/symbol\/slot\/literal\/reason/);
    const invalidReasonPath = join(root, 'scripts/invalid-reason.mjs');
    put('scripts/invalid-reason.mjs', script.replace("'Brand wordmark bytes'", "''"));
    const invalidReason = await import(invalidReasonPath);
    expect(() => invalidReason.auditCopyExceptions(inventorySource(root))).toThrow(/exact path\/symbol\/slot\/literal\/reason/);
    const original = await import(scriptPath);
    expect(original.auditCopyExceptions(inventorySource(root)).stale).toEqual(expect.arrayContaining([
      expect.objectContaining({ path: 'src/design-system/layouts/AppShell/Sidebar.tsx', symbol: 'Sidebar', slot: 'children', literal: 'Happy' }),
    ]));
  }));
  it('C5a refuses changed and additional shipping entries, restores original control', () => diskFixture((root, put) => {
    const original = readFileSync(join(root, 'index.html'), 'utf8');
    put('src/alternate-entry.tsx', 'export const Unclassified = () => <section role="dialog" />;');
    put('index.html', original.replace('/src/main.tsx', '/src/alternate-entry.tsx'));
    expect(() => inventorySource(root)).toThrow(/unsupported index.html module entry/);
    put('index.html', original.replace('</body>', '<script type="module" src="/src/alternate-entry.tsx"></script></body>'));
    expect(() => inventorySource(root)).toThrow(/one supported external module entry/);
    put('index.html', original);
    expect(inventorySource(root).mountedSymbols).toContain('src/App.tsx#App');
  }));

  it('C5b resolves configured aliases, relative barrels, wildcard reexports and literal lazy mounts; refuses config drift', () => diskFixture((root, put) => {
    put('src/App.tsx', "import { LazyOwner as Mounted } from '@/host'; export function App() { return <Mounted />; }");
    put('src/host/index.ts', "export * from './lazy';");
    put('src/host/lazy.tsx', "import { lazy } from 'react'; export const LazyOwner = lazy(() => import('../shared/dialog').then(m => ({ default: m.ActualDialog })));");
    put('src/shared/dialog.tsx', "import { forwardRef } from 'react'; export const ActualDialog = forwardRef(() => <p>{label}</p>);");
    const actual = inventorySource(root);
    expect(actual.dialogs).toEqual([expect.objectContaining({ path: 'src/shared/dialog.tsx', symbol: 'ActualDialog', consumerPath: 'src/App.tsx', consumerSymbol: 'App' })]);
    const vite = readFileSync(join(root, 'vite.config.ts'), 'utf8');
    put('vite.config.ts', vite.replace("path.resolve(__dirname, 'src')", "path.resolve(__dirname, 'alternate')"));
    expect(() => inventorySource(root)).toThrow(/Vite alias\/root\/input/);
    put('vite.config.ts', vite);
    const config = JSON.parse(readFileSync(join(root, 'tsconfig.json'), 'utf8'));
    config.compilerOptions.paths['@/*'] = ['alternate/*'];
    put('tsconfig.json', JSON.stringify(config));
    expect(() => inventorySource(root)).toThrow(/tsconfig.json alias\/root/);
    config.compilerOptions.paths['@/*'] = ['src/*'];
    put('tsconfig.json', JSON.stringify(config));
    put('src/host/lazy.tsx', "import { lazy } from 'react'; export const LazyOwner = lazy(() => import(computed));");
    expect(() => inventorySource(root)).toThrow(/unsupported computed lazy import/);
  }));

  it('C5c refuses promoted fixtures/catalogs and an ungated prototype while import-only stays unmounted', () => diskFixture((root, put) => {
    for (const file of ['src/example.test.tsx', 'src/Example.stories.tsx', 'src/test/example.tsx', 'src/lib/i18n/locales/example.tsx', 'src/prototypes/example.tsx']) {
      put(file, 'export function ExampleDialog() { return <h1>Example copy</h1>; }');
      put('src/App.tsx', `import { ExampleDialog } from './${file.slice(4)}'; export function App() { return <p>{label}</p>; }`);
      expect(inventorySource(root).dialogs).toEqual([]);
      put('src/App.tsx', `import { ExampleDialog } from './${file.slice(4)}'; export function App() { return <ExampleDialog />; }`);
      expect(() => inventorySource(root)).toThrow(/promoted fixture\/catalog\/prototype owner/);
    }
    put('src/prototypes/index.tsx', 'export function prototypeRoutes() { return <section role="dialog" />; }');
    put('src/App.tsx', "import { prototypeRoutes } from './prototypes'; export function App() { return <>{prototypeRoutes()}</>; }");
    expect(() => inventorySource(root)).toThrow(/promoted fixture/);
  }));

  it('C6 new route owners cannot borrow bare index/wildcard and computed paths refuse; exact translated identities pass', () => diskFixture((root, put) => {
    put('src/App.tsx', "import { NewOwner } from './host/new'; export function App() { return <NewOwner />; }");
    put('src/host/new.tsx', "import { Route as R } from 'react-router-dom'; export const NewOwner = () => <><R index element={<p>{label}</p>} /><R path={'*'} element={<p>{label}</p>} /></>;");
    const actual = inventorySource(root);
    expect(actual.routes.map(route => route.token)).toEqual(['index', '*']);
    expect(fullReleaseIssues(actual)).toEqual(expect.arrayContaining(['unclassified source route src/host/new.tsx:index', 'unclassified source route src/host/new.tsx:*']));
    const entry: NamespaceCoverage = { namespace: 'new', routeTokens: [], qualifiedRouteTokens: ['src/host/new.tsx:index', 'src/host/new.tsx:*'], surfaces: [], status: 'translated' };
    expect(fullReleaseIssues(actual, [entry])).toEqual([]);
    expect(fullReleaseIssues(actual, [{ ...entry, status: 'not-applicable' }])).toEqual(expect.arrayContaining([
      'not-applicable route has unproved copy-free element src/host/new.tsx:index',
    ]));
    expect(classifySourceRoute('src/host/new.tsx', '*')).toBeUndefined();
    expect(classifySourceRoute('src/routes.tsx', '*')?.status).toBe('translated');
    expect(classifySourceRoute('src/features/settings/SettingsPage.tsx', '*')?.status).toBe('not-applicable');
    put('src/host/new.tsx', "import { Route as R } from 'react-router-dom'; export function NewOwner() { return <R path={computed} element={<p>{label}</p>} />; }");
    expect(() => inventorySource(root)).toThrow(/unsupported computed route path/);
  }));

  it('C7 import-only/declaration is not mounting, alias and static render-return call are; invented and undefined owners refuse', () => diskFixture((root, put) => {
    put('src/shared/Owner.tsx', 'export function ActualDialog() { return <p>{label}</p>; }');
    put('src/App.tsx', "import { ActualDialog as AliasedDialog } from './shared/Owner'; export function App() { return <p>{label}</p>; }");
    const declared: NamespaceCoverage = { namespace: 'new', routeTokens: [], surfaces: ['ActualDialog'], status: 'translated' };
    expect(fullReleaseIssues(inventorySource(root), [declared])).toEqual(['declared but not mounted dialog new:ActualDialog']);
    for (const mount of ['<AliasedDialog />', '<>{AliasedDialog()}</>']) {
      put('src/App.tsx', `import { ActualDialog as AliasedDialog } from './shared/Owner'; export function App() { return ${mount}; }`);
      const actual = inventorySource(root);
      expect(actual.mountedSymbols).toContain('src/shared/Owner.tsx#ActualDialog');
      // Static render-return calls still need a declared site, not only a name.
      expect(actual.dialogs).toHaveLength(1);
      expect(fullReleaseIssues(actual, [declared])[0]).toMatch(/unclassified mounted surface/);
      expect(fullReleaseIssues(actual, [{ ...declared, qualifiedSurfaces: actual.dialogs.map(sourceSurfaceIdentity) }])).toEqual([]);
    }
    put('src/App.tsx', 'export function App() { return <InventedDialog />; }');
    expect(() => inventorySource(root)).toThrow(/undefined component/);
  }));

  it('C7 inline dialog sites and new containers require source ownership; full release rejects every english-only namespace', () => diskFixture((root, put) => {
    put('src/App.tsx', "import { Container } from './host/Container'; export function App() { return <Container />; }");
    put('src/host/Container.tsx', 'export const Container = () => <section role="dialog"><h1>{label}</h1></section>;');
    const actual = inventorySource(root);
    expect(actual.dialogs).toEqual([expect.objectContaining({ path: 'src/host/Container.tsx', symbol: 'Container', site: 'role:dialog' })]);
    expect(fullReleaseIssues(actual, [])[0]).toMatch(/unclassified mounted surface/);
    const entry: NamespaceCoverage = { namespace: 'new', routeTokens: [], surfaces: [], qualifiedSurfaces: actual.dialogs.map(sourceSurfaceIdentity), status: 'english-only' };
    expect(fullReleaseIssues(actual, [entry])).toEqual(['full release refuses english-only namespace new']);
    expect(fullReleaseIssues(actual, [{ ...entry, status: 'translated' }])).toEqual([]);
    expect(fullReleaseIssues(actual, [{ ...entry, qualifiedSurfaces: ['src/host/Ghost.tsx#Ghost->src/shared/Ghost.tsx#GhostDialog:component'] }])).toEqual(expect.arrayContaining([expect.stringContaining('declared but not mounted surface')]));
    put('src/host/Container.tsx', 'export const Container = () => <><section role="dialog" /><section role="dialog" /></>;');
    const twoSites = inventorySource(root);
    expect(twoSites.dialogs.map(site => site.site)).toEqual(['role:dialog', 'role:dialog:2']);
    expect(fullReleaseIssues(twoSites, [{ ...entry, status: 'translated' }])).toEqual(expect.arrayContaining([
      expect.stringContaining('role:dialog:2'),
    ]));
  }));
});
