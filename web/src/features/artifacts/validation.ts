/**
 * Client-side guards for the artifacts upload form. These mirror the daemon's
 * artifact constraints (CLAUDE.md "Shared Artifacts") so the founder gets an
 * inline error instead of a 400/413 round-trip.
 */

/** Per-file size cap — keep in sync with `MAX_ARTIFACT_BYTES` in the daemon. */
export const MAX_ARTIFACT_BYTES = 10 * 1024 * 1024;
export const MAX_ARTIFACT_NAME_LENGTH = 200;
/** Per-segment char set — each segment between '/' separators must match. */
export const ARTIFACT_NAME_RE = /^[A-Za-z0-9._-]+$/;

/**
 * Validate an upload before it is sent. Returns a human-readable error
 * message, or `null` when the upload is acceptable.
 */
export function validateArtifactUpload(input: {
  name: string;
  sizeBytes: number;
}): string | null {
  const { name, sizeBytes } = input;
  if (!name) return 'A file name is required.';
  if (name.length > MAX_ARTIFACT_NAME_LENGTH) {
    return `Name must be at most ${MAX_ARTIFACT_NAME_LENGTH} characters.`;
  }
  if (name.startsWith('/') || name.endsWith('/') || name.includes('//') || name.includes('\\')) {
    return 'Name may contain only letters, digits, dot, underscore, hyphen, and forward slash as separator.';
  }
  for (const seg of name.split('/')) {
    if (!seg || seg === '..' || seg.startsWith('.')) {
      return 'Name may contain only letters, digits, dot, underscore, hyphen, and forward slash as separator.';
    }
    if (!ARTIFACT_NAME_RE.test(seg)) {
      return 'Name may contain only letters, digits, dot, underscore, hyphen, and forward slash as separator.';
    }
  }
  if (sizeBytes > MAX_ARTIFACT_BYTES) {
    return 'File exceeds the 10 MB limit.';
  }
  return null;
}
