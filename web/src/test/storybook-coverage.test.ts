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
  const visited = new Set<ts.Node>();
  function renders(node: ts.Node): boolean {
    if (visited.has(node)) return false;
    visited.add(node);

    if (ts.isJsxOpeningElement(node) || ts.isJsxSelfClosingElement(node)) {
      if (ts.isIdentifier(node.tagName)) {
        if (importedNames.has(node.tagName.text)) return true;
        const local = localDeclarations.get(node.tagName.text);
        if (local && renders(local)) return true;
      }
    }
    if (ts.isCallExpression(node) && ts.isIdentifier(node.expression)) {
      if (importedNames.has(node.expression.text)) return true;
      const local = localDeclarations.get(node.expression.text);
      if (local && renders(local)) return true;
    }

    return node.getChildren(sourceFile).some(renders);
  }

  const initializer = declaration.initializer;
  if (ts.isArrowFunction(initializer) || ts.isFunctionExpression(initializer)) return renders(initializer);
  if (!ts.isObjectLiteralExpression(initializer)) return false;

  return initializer.properties.some((property) => {
    if (!ts.isPropertyAssignment(property) || !ts.isIdentifier(property.name)) return false;
    if (property.name.text === 'render') return renders(property.initializer);
    if (property.name.text !== 'component' || !ts.isIdentifier(property.initializer)) return false;
    return importedNames.has(property.initializer.text);
  });
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

  test('an imported component referenced only in story metadata while another component renders is rejected', () => {
    const storyFile = join(designSystemRoot, 'patterns/Adversarial.stories.tsx');
    const component = { name: 'TaskCard', source: join(designSystemRoot, 'patterns/TaskCard.tsx') };
    const placeholder = [
      "import { Button } from '../primitives/Button';",
      "import { TaskCard } from './TaskCard';",
      "export const TaskCardPlaceholder = { parameters: { componentName: TaskCard.name }, render: () => <Button /> };",
    ].join('\n');
    expect(importedAndRendered(placeholder, storyFile, 'TaskCardPlaceholder', component)).toBe(false);
  });

  test.each([
    ['inline JSX render', "export const TaskCardStory = { render: () => <TaskCard /> };"],
    ['local render helper', "const Example = () => <TaskCard />; export const TaskCardStory = { render: () => <Example /> };"],
    ['CSF component field', "export const TaskCardStory = { component: TaskCard };"],
  ])('accepts the exact imported component through a supported %s', (_form, story) => {
    const storyFile = join(designSystemRoot, 'patterns/Positive.stories.tsx');
    const component = { name: 'TaskCard', source: join(designSystemRoot, 'patterns/TaskCard.tsx') };
    const storyCode = `import { TaskCard } from './TaskCard';\n${story}`;
    expect(importedAndRendered(storyCode, storyFile, 'TaskCardStory', component)).toBe(true);
  });

  test('coverage ledger contains every reusable component exactly once', () => {
    for (const { name } of reusableComponents) {
      expect(guide.split(`| \`${name}\` |`).length - 1, `${name} ledger rows`).toBe(1);
    }
  });
});
