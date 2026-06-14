import { describe, expect, test } from 'vitest';
import {
  MAX_ARTIFACT_BYTES,
  MAX_ARTIFACT_NAME_LENGTH,
  validateArtifactUpload,
} from './validation';

describe('validateArtifactUpload', () => {
  test('rejects files over the 10 MB cap', () => {
    expect(
      validateArtifactUpload({ name: 'report.pdf', sizeBytes: MAX_ARTIFACT_BYTES + 1 }),
    ).toMatch(/10 MB/);
  });

  test('rejects names with characters outside [A-Za-z0-9._-/]', () => {
    expect(
      validateArtifactUpload({ name: 'my report.pdf', sizeBytes: 10 }),
    ).toMatch(/letters, digits/);
    // Trailing slash rejected.
    expect(
      validateArtifactUpload({ name: 'bad/trailing/', sizeBytes: 10 }),
    ).toMatch(/letters, digits/);
  });

  test('accepts nested keys with forward slashes', () => {
    expect(
      validateArtifactUpload({
        name: 'reports/2026/q2.pdf',
        sizeBytes: MAX_ARTIFACT_BYTES,
      }),
    ).toBeNull();
    expect(
      validateArtifactUpload({
        name: 'a/b/c.txt',
        sizeBytes: 10,
      }),
    ).toBeNull();
  });

  test('rejects traversal vectors in nested names', () => {
    expect(
      validateArtifactUpload({ name: '/leading-slash', sizeBytes: 10 }),
    ).toMatch(/letters, digits/);
    expect(
      validateArtifactUpload({ name: 'a//b', sizeBytes: 10 }),
    ).toMatch(/letters, digits/);
    expect(
      validateArtifactUpload({ name: 'reports/../evil', sizeBytes: 10 }),
    ).toMatch(/letters, digits/);
    expect(
      validateArtifactUpload({ name: '.hidden/x', sizeBytes: 10 }),
    ).toMatch(/letters, digits/);
  });

  test('rejects names longer than 200 characters', () => {
    const longName = `${'a'.repeat(MAX_ARTIFACT_NAME_LENGTH + 1)}.bin`;
    expect(validateArtifactUpload({ name: longName, sizeBytes: 10 })).toMatch(
      /200 characters/,
    );
  });

  test('accepts a valid name at the size cap', () => {
    expect(
      validateArtifactUpload({
        name: 'dev_agent-2026-06-10-perf-report.pdf',
        sizeBytes: MAX_ARTIFACT_BYTES,
      }),
    ).toBeNull();
  });
});
