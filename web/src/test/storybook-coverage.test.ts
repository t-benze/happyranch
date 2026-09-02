import { readFileSync, readdirSync } from 'node:fs';
import { dirname, join, normalize, relative, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import ts from 'typescript';

const here = dirname(fileURLToPath(import.meta.url));
const webRoot = join(here, '../..');
const designSystemRoot = join(webRoot, 'src/design-system');
const guide = readFileSync(join(webRoot, 'DESIGN_SYSTEM.md'), 'utf8');

function filesBelow(directory: string): string[] {
  return readdirSync(directory, { withFileTypes: true }).flatMap((entry) => {
    const path = join(directory, entry.name);
    return entry.isDirectory() ? filesBelow(path) : [path];
  });
}

type ReusableComponent = { name: string; source: string };
type StoryMapping = { storyFile: string; storyExport: string };

const reusableComponents: ReusableComponent[] = filesBelow(designSystemRoot)
  .filter((path) => /\/(primitives|patterns|layouts)\/[^/]+\.tsx$/.test(path))
  .filter((path) => !/\.(test|stories)\.tsx$/.test(path))
  .map((source) => ({ name: source.match(/([^/]+)\.tsx$/)?.[1] ?? '', source }))
  .filter(({ name }) => Boolean(name));

function ledgerEntry(name: string): string {
  const row = guide.split('\n').find((line) => line.startsWith(`| \`${name}\` |`));
  if (!row) throw new Error(`Missing DESIGN_SYSTEM.md ledger row for ${name}`);
  return row;
}

function storyMapping(row: string): StoryMapping | null {
  const match = row.match(/\[story:([^#\]]+)#([^\]]+)\]/);
  return match ? { storyFile: match[1], storyExport: match[2] } : null;
}

function importedAndRendered(
  storyCode: string,
  storyFile: string,
  storyExport: string,
  component: ReusableComponent,
): boolean {
  const sourceFile = ts.createSourceFile(storyFile, storyCode, ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX);
  const importedNames = new Set<string>();
  for (const statement of sourceFile.statements) {
    if (!ts.isImportDeclaration(statement) || !statement.importClause || !ts.isStringLiteral(statement.moduleSpecifier)) continue;
    const resolvedImport = resolve(dirname(storyFile), statement.moduleSpecifier.text);
    const expected = component.source.replace(/\.tsx$/, '');
    if (normalize(resolvedImport) !== normalize(expected)) continue;
    const bindings = statement.importClause.namedBindings;
    if (bindings && ts.isNamedImports(bindings)) {
      for (const element of bindings.elements) importedNames.add(element.name.text);
    }
    if (statement.importClause.name) importedNames.add(statement.importClause.name.text);
  }

  const declaration = sourceFile.statements
    .filter(ts.isVariableStatement)
    .flatMap((statement) => [...statement.declarationList.declarations])
    .find((item) => ts.isIdentifier(item.name) && item.name.text === storyExport);
  if (!declaration?.initializer || importedNames.size === 0) return false;

  const localDeclarations = new Map<string, ts.Node>();
  for (const statement of sourceFile.statements) {
    if (ts.isFunctionDeclaration(statement) && statement.name) localDeclarations.set(statement.name.text, statement);
    if (ts.isVariableStatement(statement)) {
      for (const item of statement.declarationList.declarations) {
        if (ts.isIdentifier(item.name) && item.initializer) localDeclarations.set(item.name.text, item.initializer);
      }
    }
  }
  let rendered = false;
  const visited = new Set<ts.Node>();
  function visit(node: ts.Node): void {
    if (visited.has(node)) return;
    visited.add(node);
    if (ts.isIdentifier(node) && importedNames.has(node.text)) rendered = true;
    if (ts.isIdentifier(node)) {
      const local = localDeclarations.get(node.text);
      if (local) visit(local);
    }
    ts.forEachChild(node, visit);
  }
  visit(declaration.initializer);
  return rendered;
}

describe('Storybook design-system coverage', () => {
  test('every discovered reusable source has one explicit story or justified exclusion mapping', () => {
    for (const component of reusableComponents) {
      const row = ledgerEntry(component.name);
      const mapping = storyMapping(row);
      const exclusion = row.includes(`[excluded:${component.name}]`);
      expect(Number(Boolean(mapping)) + Number(exclusion), `${component.name} mapping count`).toBe(1);
    }
  });

  test('every story mapping imports and renders its named component', () => {
    for (const component of reusableComponents) {
      const mapping = storyMapping(ledgerEntry(component.name));
      if (!mapping) continue;
      const storyFile = join(designSystemRoot, mapping.storyFile);
      const storyCode = readFileSync(storyFile, 'utf8');
      expect(
        importedAndRendered(storyCode, storyFile, mapping.storyExport, component),
        `${component.name} -> ${relative(webRoot, storyFile)}#${mapping.storyExport}`,
      ).toBe(true);
    }
  });

  test('a merely named placeholder rendering another component is rejected', () => {
    const storyFile = join(designSystemRoot, 'patterns/Adversarial.stories.tsx');
    const component = { name: 'TaskCard', source: join(designSystemRoot, 'patterns/TaskCard.tsx') };
    const placeholder = "import { Button } from '../primitives/Button'; export const TaskCardPlaceholder = { render: () => <Button /> };";
    expect(importedAndRendered(placeholder, storyFile, 'TaskCardPlaceholder', component)).toBe(false);
  });

  test('coverage ledger contains every reusable component exactly once', () => {
    for (const { name } of reusableComponents) {
      expect(guide.split(`| \`${name}\` |`).length - 1, `${name} ledger rows`).toBe(1);
    }
  });
});
