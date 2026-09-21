import { readdirSync, readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { describe, expect, it } from 'vitest';
import {
  classifyRouteIdentity,
  classifyRouteToken,
  COVERAGE_MANIFEST,
  coverageSummary,
  describeCoverage,
  extractRouteTokens,
  NOT_APPLICABLE_NAMESPACES,
  unclassifiedRouteIdentities,
  unclassifiedRouteTokens,
} from './coverage';

const here = dirname(fileURLToPath(import.meta.url));
const webRoot = join(here, '../../..');

function read(relativePath: string): string {
  return readFileSync(join(webRoot, relativePath), 'utf8');
}

/**
 * Route modules and (for the modules whose `*`/`index` tokens collide with
 * another module) the qualified identity scope. The scan reads the REAL route
 * sources — it never repeats a hand-written token list.
 */
const ROUTE_SOURCES: ReadonlyArray<{ file: string; scope?: string }> = [
  { file: 'src/routes.tsx' },
  { file: 'src/prototypes/index.tsx' },
  { file: 'src/features/settings/SettingsPage.tsx', scope: 'SettingsPage.tsx' },
];

function scannedIdentities(): string[] {
  return ROUTE_SOURCES.flatMap(({ file, scope }) =>
    extractRouteTokens(read(file)).map((token) => (scope ? `${scope}:${token}` : token)),
  );
}

function surfacesFor(namespace: string): readonly string[] {
  const entry = COVERAGE_MANIFEST.find((candidate) => candidate.namespace === namespace);
  if (!entry) throw new Error(`manifest is missing namespace ${namespace}`);
  return entry.surfaces;
}

function namespaceStatus(namespace: string): string | undefined {
  return COVERAGE_MANIFEST.find((entry) => entry.namespace === namespace)?.status;
}

/** Actual JSX dialog mounts in a source (ignores generic type arguments). */
function mountedDialogs(source: string): string[] {
  const names = new Set<string>();
  for (const match of source.matchAll(/(?:^|[\s({>])<([A-Z][A-Za-z0-9]*Dialog)\b/g)) {
    names.add(match[1]);
  }
  return [...names];
}

/** Real dialog component definitions under `src/` (no test files). */
function definedDialogs(): Set<string> {
  const found = new Set<string>();
  const walk = (directory: string): void => {
    for (const entry of readdirSync(directory, { withFileTypes: true })) {
      const path = join(directory, entry.name);
      if (entry.isDirectory()) {
        walk(path);
        continue;
      }
      if (!/\.tsx?$/.test(entry.name) || /\.test\.tsx?$/.test(entry.name)) continue;
      const source = readFileSync(path, 'utf8');
      for (const match of source.matchAll(/(?:export\s+)?function\s+([A-Z][A-Za-z0-9]*Dialog)\b/g)) {
        found.add(match[1]);
      }
      for (const match of source.matchAll(/(?:export\s+)?const\s+([A-Z][A-Za-z0-9]*Dialog)\s*[:=]/g)) {
        found.add(match[1]);
      }
    }
  };
  walk(join(webRoot, 'src'));
  return found;
}

/**
 * Namespace -> the REAL page sources that mount its dialogs. Anchoring the
 * manifest to these sources (instead of a hand-written list) is what fails an
 * invented dialog name.
 */
const DIALOG_CONSUMER_SOURCES: Readonly<Record<string, readonly string[]>> = {
  threads: ['src/features/threads/ThreadsPage.tsx'],
  tasks: ['src/features/tasks/TaskDetailPage.tsx'],
  jobs: ['src/features/jobs/JobDetailPage.tsx'],
  agents: ['src/features/agents/AgentsPage.tsx'],
  kb: ['src/features/kb/KbPage.tsx'],
  todos: ['src/features/todos/TodoDetailPage.tsx'],
  'work-hours': [
    'src/features/work-hours-config/OverviewPage.tsx',
    'src/features/work-hours-config/AgentDetailPage.tsx',
  ],
  settings: [
    'src/features/settings/sections/OrganizationSection.tsx',
    'src/features/settings/sections/AssistantSection.tsx',
  ],
  'app-shell': ['src/design-system/layouts/AppShell/Sidebar.tsx'],
};

describe('coverage manifest (W1 acceptance case 7)', () => {
  it('classifies every route token in the real route modules', () => {
    const bareTokens = ROUTE_SOURCES.filter(({ scope }) => scope === undefined).flatMap(({ file }) =>
      extractRouteTokens(read(file)),
    );
    expect(bareTokens.length).toBeGreaterThan(0);
    expect(unclassifiedRouteTokens(bareTokens)).toEqual([]);
  });

  it('classifies every qualified identity for colliding tokens', () => {
    const identities = scannedIdentities();
    expect(identities).toContain('SettingsPage.tsx:*');
    expect(identities).toContain('SettingsPage.tsx:index');
    expect(unclassifiedRouteIdentities(identities)).toEqual([]);
  });

  it('treats the copy-bearing catch-all/root shell as english-only, never not-applicable', () => {
    // Reviewer red proof: `*` renders "Not found. Go home" and `index` renders
    // the RootRedirect "Loading…" — both are user-facing English copy.
    expect(classifyRouteToken('*')?.status).toBe('english-only');
    expect(classifyRouteToken('index')?.status).toBe('english-only');
    expect(classifyRouteIdentity('routes.tsx:*')?.status).toBe('english-only');
    const routes = read('src/routes.tsx');
    expect(routes).toContain('Not found.');
    expect(routes).toContain('Go home');
    expect(routes).toContain('Loading…');
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

  it('keeps the Settings copy-bearing subroutes english-only', () => {
    for (const token of ['settings/*', 'assistant', 'daemon-capacity', 'organization', 'executors']) {
      expect(classifyRouteToken(token)?.status, token).toBe('english-only');
    }
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

  it('leaves all not-yet-migrated surfaces marked incomplete (no route-wide Chinese claim)', () => {
    const summary = coverageSummary();
    expect(summary.translated).toBe(0);
    expect(summary.englishOnly).toBeGreaterThan(0);
    for (const entry of COVERAGE_MANIFEST) {
      expect(['translated', 'english-only', 'not-applicable']).toContain(entry.status);
      if (NOT_APPLICABLE_NAMESPACES.includes(entry.namespace)) {
        expect(entry.status, `namespace ${entry.namespace}`).toBe('not-applicable');
      } else {
        expect(entry.status, `namespace ${entry.namespace}`).toBe('english-only');
      }
    }
  });

  it('renders the visible English marker for incomplete surfaces', () => {
    expect(describeCoverage('en', 'english-only')).toBe('English only — not yet migrated');
    expect(describeCoverage('zh-CN', 'english-only')).toBe('仅英文 — 尚未迁移');
    expect(describeCoverage('en', 'not-applicable')).toBe('No user-facing copy');
  });

  it('anchors every manifest dialog surface to a real mounted consumer', () => {
    for (const [namespace, sources] of Object.entries(DIALOG_CONSUMER_SOURCES)) {
      const declared = surfacesFor(namespace);
      for (const file of sources) {
        for (const dialog of mountedDialogs(read(file))) {
          expect(declared, `${namespace} must declare mounted ${dialog} from ${file}`).toContain(
            dialog,
          );
        }
      }
    }
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
