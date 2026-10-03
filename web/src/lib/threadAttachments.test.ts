import { describe, expect, test, vi } from 'vitest';
import {
  MAX_THREAD_ATTACHMENTS,
  allocateArtifactName,
  attachmentContentType,
  createSelectionIdFactory,
  formatAttachmentSize,
  safeArtifactName,
} from './threadAttachments';

describe('thread attachment helpers', () => {
  test('exports the backend attachment cap used by upload controls', () => {
    expect(MAX_THREAD_ATTACHMENTS).toBe(5);
  });

  test('formats byte sizes for display', () => {
    expect(formatAttachmentSize(512)).toBe('512 B');
    expect(formatAttachmentSize(1536)).toBe('1.5 KB');
    expect(formatAttachmentSize(5 * 1024 * 1024)).toBe('5 MB');
    expect(formatAttachmentSize(null)).toBeNull();
    expect(formatAttachmentSize(Number.POSITIVE_INFINITY)).toBeNull();
  });

  test('uses browser file type metadata when available', () => {
    expect(
      attachmentContentType(new File(['pdf'], 'report.pdf', { type: 'application/pdf' })),
    ).toBe('application/pdf');
    expect(attachmentContentType(new File(['data'], 'unknown.bin'))).toBeNull();
  });

  test('sanitizes generated artifact names', () => {
    const file = new File(['x'], '../weird name?.pdf');
    expect(safeArtifactName('THR-001', file, 2)).toMatch(
      /^THR-001-\d{8}T\d{6}Z-2-weird-name-.pdf$/,
    );
  });

  test('selection ids are monotonic and not derived from file metadata', () => {
    const next = createSelectionIdFactory();
    expect(next()).toBe('sel-1');
    expect(next()).toBe('sel-2');
    const prefixed = createSelectionIdFactory('nsel');
    expect(prefixed()).toBe('nsel-1');
    // Two identical Files still get distinct selection identities.
    const a = new File(['x'], 'same.txt', { lastModified: 1 });
    const b = new File(['x'], 'same.txt', { lastModified: 1 });
    expect(next()).not.toBe(next());
    expect(a.name).toBe(b.name);
  });

  test('name reservation avoids a retained name and picks the next collision index', () => {
    const file = new File(['x'], 'a b.txt');
    const first = allocateArtifactName('THR-1', file, new Set());
    const second = allocateArtifactName('THR-1', file, new Set([first]));
    expect(second).not.toBe(first);
    expect(second).toMatch(/-2-a-b\.txt$/);
  });

  test('name reservation avoids names already allocated by the page', () => {
    const file = new File(['x'], 'a-b.txt');
    const first = allocateArtifactName('THR-1', file, new Set());
    const second = allocateArtifactName('THR-1', file, new Set(), new Set([first]));
    expect(second).not.toBe(first);
  });

  test('equal-metadata files with different bytes reserve distinct names', () => {
    const a = new File(['AAA'], 'same.txt', { lastModified: 7 });
    const b = new File(['BBB'], 'same.txt', { lastModified: 7 });
    const nameA = allocateArtifactName('THR-1', a, new Set());
    const nameB = allocateArtifactName('THR-1', b, new Set([nameA]));
    expect(nameA).not.toBe(nameB);
  });

  // TASK-8616 C3.3 boundary: the old `index <= 1000` ceiling returned index
  // 1001 without checking it, so a page that had already reserved 1001 was
  // handed an occupied name (a later `put` would overwrite retained bytes).
  test('never returns an occupied name at and beyond the 1000-index boundary', () => {
    vi.useFakeTimers({ toFake: ['Date'] });
    vi.setSystemTime(new Date('2026-09-21T00:00:00Z'));
    try {
      const file = new File(['x'], 'a b.txt');
      const occupied = new Set<string>();
      for (let index = 1; index <= 1001; index += 1) {
        occupied.add(safeArtifactName('THR-1', file, index));
      }
      const allocated = allocateArtifactName('THR-1', file, occupied);
      expect(occupied.has(allocated)).toBe(false);
      expect(allocated).toBe(safeArtifactName('THR-1', file, 1002));
    } finally {
      vi.useRealTimers();
    }
  });
});
