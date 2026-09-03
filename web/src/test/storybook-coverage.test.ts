import { readFileSync, readdirSync } from 'node:fs';
import { dirname, join, relative } from 'node:path';
import { fileURLToPath } from 'node:url';

const here = dirname(fileURLToPath(import.meta.url));
const webRoot = join(here, '../..');
const designSystemRoot = join(webRoot, 'src/design-system');
const guide = readFileSync(join(webRoot, 'DESIGN_SYSTEM.md'), 'utf8');

const componentModules = import.meta.glob<Record<string, unknown>>(
  ['../design-system/{primitives,patterns,layouts}/**/*.tsx', '!../design-system/**/*.test.tsx', '!../design-system/**/*.stories.tsx'],
  { eager: true },
);
const storyModules = import.meta.glob<Record<string, unknown>>('../design-system/**/*.stories.tsx', { eager: true });

function filesBelow(directory: string): string[] {
  return readdirSync(directory, { withFileTypes: true }).flatMap((entry) => {
    const path = join(directory, entry.name);
    return entry.isDirectory() ? filesBelow(path) : [path];
  });
}

type ReusableComponent = { name: string; source: string };
type ModuleExport = { file: string; exportName: string };
type LedgerMapping = { source: ModuleExport; story: ModuleExport };

const reusableComponents: ReusableComponent[] = filesBelow(designSystemRoot)
  .filter((path) => /\/(primitives|patterns|layouts)\/.+\.tsx$/.test(path))
  .filter((path) => !/\.(test|stories)\.tsx$/.test(path))
  .map((source) => ({ name: source.match(/([^/]+)\.tsx$/)?.[1] ?? '', source }))
  .filter(({ name }) => Boolean(name));

function ledgerEntry(name: string): string {
  const row = guide.split('\n').find((line) => line.startsWith(`| \`${name}\` |`));
  if (!row) throw new Error(`Missing DESIGN_SYSTEM.md ledger row for ${name}`);
  return row;
}

function moduleExport(row: string, kind: 'source' | 'story'): ModuleExport | null {
  const match = row.match(new RegExp(`\\[${kind}:([^#\\]]+)#([^\\]]+)\\]`));
  return match ? { file: match[1], exportName: match[2] } : null;
}

function ledgerMapping(row: string): LedgerMapping | null {
  const source = moduleExport(row, 'source');
  const story = moduleExport(row, 'story');
  return source && story ? { source, story } : null;
}

function viteKey(file: string): string {
  return `../design-system/${file}`;
}

function runtimeCoverageError(component: unknown, storyModule: Record<string, unknown>, storyExport: string): string | null {
  const meta = storyModule.default;
  if (!meta || typeof meta !== 'object') return 'story module must have an object default meta export';
  if (!('component' in meta)) return 'default meta must declare component';
  if (meta.component !== component) return 'default meta.component must be the exact source export object';

  const story = storyModule[storyExport];
  if (!story || typeof story !== 'object') return `missing named story export ${storyExport}`;
  if ('render' in story && story.render !== undefined) return `${storyExport} must use Storybook's default component render`;
  return null;
}

describe('Storybook design-system coverage', () => {
  test('every discovered reusable source has one explicit story or justified exclusion mapping', () => {
    for (const component of reusableComponents) {
      const row = ledgerEntry(component.name);
      const mapping = ledgerMapping(row);
      const exclusion = row.includes(`[excluded:${component.name}]`);
      expect(Number(Boolean(mapping)) + Number(exclusion), `${component.name} mapping count`).toBe(1);
    }
  });

  test('every story mapping uses exact runtime component identity and a default-render story', () => {
    for (const component of reusableComponents) {
      const mapping = ledgerMapping(ledgerEntry(component.name));
      if (!mapping) continue;
      expect(mapping.source.file, `${component.name} source path`).toBe(relative(designSystemRoot, component.source));
      const sourceModule = componentModules[viteKey(mapping.source.file)];
      const storyModule = storyModules[viteKey(mapping.story.file)];
      expect(sourceModule, `${component.name} source module ${mapping.source.file}`).toBeDefined();
      expect(storyModule, `${component.name} story module ${mapping.story.file}`).toBeDefined();
      const sourceExport = sourceModule?.[mapping.source.exportName];
      expect(sourceExport, `${component.name} source export ${mapping.source.exportName}`).toBeDefined();
      expect(
        runtimeCoverageError(sourceExport, storyModule ?? {}, mapping.story.exportName),
        `${component.name} -> ${mapping.story.file}#${mapping.story.exportName}`,
      ).toBeNull();
    }
  });

  test.each([
    ['same-name lexical shadowing', () => { const TaskCard = () => null; return { source: () => null, story: { default: { component: TaskCard }, Coverage: {} } }; }],
    ['aliased import wired to the wrong component', () => { const SourceAlias = () => null; const Other = () => null; return { source: SourceAlias, story: { default: { component: Other }, Coverage: {} } }; }],
    ['metadata/name-only reference', () => { const Source = () => null; return { source: Source, story: { default: { title: Source.name }, Coverage: { parameters: { componentName: Source.name } } } }; }],
    ['custom render returning another component', () => { const Source = () => null; const Other = () => null; return { source: Source, story: { default: { component: Source }, Coverage: { render: () => Other() } } }; }],
    ['missing default meta', () => { const Source = () => null; return { source: Source, story: { Coverage: {} } }; }],
    ['default meta mismatch', () => { const Source = () => null; return { source: Source, story: { default: { component: () => null }, Coverage: {} } }; }],
  ])('fails closed for %s', (_case, fixture) => {
    const { source, story } = fixture();
    expect(runtimeCoverageError(source, story, 'Coverage')).not.toBeNull();
  });

  test('accepts an aliased import only when meta identity is exact and the story uses default render', () => {
    const ImportedUnderAlias = () => null;
    expect(runtimeCoverageError(ImportedUnderAlias, { default: { component: ImportedUnderAlias }, Coverage: {} }, 'Coverage')).toBeNull();
  });

  test('coverage ledger contains every reusable component exactly once with the expected totals', () => {
    for (const { name } of reusableComponents) {
      expect(guide.split(`| \`${name}\` |`).length - 1, `${name} ledger rows`).toBe(1);
    }
    const rows = reusableComponents.map(({ name }) => ledgerEntry(name));
    expect(rows.filter((row) => ledgerMapping(row)).length).toBe(40);
    expect(rows.filter((row) => row.includes('[excluded:')).length).toBe(4);
  });
});
