import { describe, expect, test } from 'vitest';
import {
  MAX_THREAD_ATTACHMENTS,
  attachmentContentType,
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
});
