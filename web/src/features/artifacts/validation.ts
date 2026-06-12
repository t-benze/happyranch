/**
 * Client-side guards for the artifacts upload form. These mirror the daemon's
 * artifact constraints (CLAUDE.md "Shared Assets") so the founder gets an
 * inline error instead of a 400/413 round-trip.
 */

/** Per-file size cap — keep in sync with `MAX_ARTIFACT_BYTES` in the daemon. */
export const MAX_ARTIFACT_BYTES = 10 * 1024 * 1024;
export const MAX_ARTIFACT_NAME_LENGTH = 200;
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
  if (!ARTIFACT_NAME_RE.test(name)) {
    return 'Name may contain only letters, digits, dot, underscore, and hyphen.';
  }
  if (sizeBytes > MAX_ARTIFACT_BYTES) {
    return 'File exceeds the 10 MB limit.';
  }
  return null;
}
