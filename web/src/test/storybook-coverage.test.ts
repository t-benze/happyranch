import { readFileSync, readdirSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

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

const reusableComponents = filesBelow(designSystemRoot)
  .filter((path) => /\.tsx$/.test(path))
  .filter((path) => /\/(primitives|patterns|layouts)\//.test(path))
  .filter((path) => !/\.(test|stories)\.tsx$/.test(path))
  .map((path) => path.match(/([^/]+)\.tsx$/)?.[1])
  .filter((name): name is string => Boolean(name));

const storySource = filesBelow(designSystemRoot)
  .filter((path) => path.endsWith('.stories.tsx'))
  .map((path) => readFileSync(path, 'utf8'))
  .join('\n');

describe('Storybook design-system coverage', () => {
  test('every reusable component has a named story or justified exclusion', () => {
    const missing = reusableComponents.filter((name) => {
      const namedStory = new RegExp(`export const ${name}[A-Za-z0-9_]*\\s*:`).test(storySource);
      return !namedStory && !guide.includes(`[excluded:${name}]`);
    });
    expect(missing).toEqual([]);
  });

  test('coverage ledger contains every reusable component exactly once', () => {
    for (const name of reusableComponents) {
      const rowCount = guide.split(`| \`${name}\` |`).length - 1;
      expect(rowCount, `${name} ledger rows`).toBe(1);
    }
  });

  test('exclusions are not silently also counted as stories', () => {
    const overlaps = reusableComponents.filter((name) =>
      guide.includes(`[excluded:${name}]`) &&
      new RegExp(`export const ${name}[A-Za-z0-9_]*\\s*:`).test(storySource),
    );
    expect(overlaps).toEqual([]);
  });
});
