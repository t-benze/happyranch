import { describe, expect, test } from 'vitest';
import { buildFolderView, hasFolders, type ArtifactItem } from './artifact-tree';

function art(name: string, modified_at = '2026-06-10T00:00:00Z', size_bytes = 1024): ArtifactItem {
  return { name, size_bytes, modified_at };
}

const TREE: ArtifactItem[] = [
  art('patches/dev_agent-2026-06-16-settings.patch', '2026-06-16T00:00:00Z'),
  art('patches/dev_agent-2026-06-14-runtime.patch', '2026-06-14T00:00:00Z'),
  art('reports/qa/qa_engineer-2026-06-14-parity.md', '2026-06-14T00:00:00Z'),
  art('reports/code_reviewer-2026-06-15-notes.md', '2026-06-15T00:00:00Z'),
  art('release-checklist.md', '2026-06-16T00:00:00Z'),
];

describe('hasFolders', () => {
  test('true only when some name carries a path segment', () => {
    expect(hasFolders(TREE)).toBe(true);
    expect(hasFolders([art('a.md'), art('b.txt')])).toBe(false);
    expect(hasFolders([])).toBe(false);
  });
});

describe('buildFolderView', () => {
  test('root: immediate subfolders (with recursive counts) + direct files', () => {
    const { folders, files } = buildFolderView(TREE, '');
    expect(folders.map((f) => [f.name, f.path, f.count])).toEqual([
      ['patches', 'patches', 2],
      ['reports', 'reports', 2],
    ]);
    // Only the flat file sits directly at root.
    expect(files.map((f) => f.name)).toEqual(['release-checklist.md']);
  });

  test('sorts folders by name and files by modified_at (recent first)', () => {
    const { files } = buildFolderView(TREE, 'patches');
    expect(files.map((f) => f.name)).toEqual([
      'patches/dev_agent-2026-06-16-settings.patch',
      'patches/dev_agent-2026-06-14-runtime.patch',
    ]);
  });

  test('nested folder: surfaces a deeper subfolder plus the files at that level', () => {
    const { folders, files } = buildFolderView(TREE, 'reports');
    expect(folders.map((f) => [f.name, f.path, f.count])).toEqual([['qa', 'reports/qa', 1]]);
    expect(files.map((f) => f.name)).toEqual(['reports/code_reviewer-2026-06-15-notes.md']);
  });

  test('a folder prefixing another name is not double-counted as a file', () => {
    const { folders, files } = buildFolderView(TREE, 'reports/qa');
    expect(folders).toEqual([]);
    expect(files.map((f) => f.name)).toEqual(['reports/qa/qa_engineer-2026-06-14-parity.md']);
  });

  test('breadcrumb: always starts at the Artifacts root and appends each segment', () => {
    expect(buildFolderView(TREE, '').crumbs).toEqual([{ label: 'Artifacts', path: '' }]);
    expect(buildFolderView(TREE, 'reports/qa').crumbs).toEqual([
      { label: 'Artifacts', path: '' },
      { label: 'reports', path: 'reports' },
      { label: 'qa', path: 'reports/qa' },
    ]);
  });

  test('flat-only input renders as root files with no folders', () => {
    const flat = [art('a.md', '2026-06-02T00:00:00Z'), art('b.txt', '2026-06-09T00:00:00Z')];
    const { folders, files } = buildFolderView(flat, '');
    expect(folders).toEqual([]);
    expect(files.map((f) => f.name)).toEqual(['b.txt', 'a.md']);
  });
});
