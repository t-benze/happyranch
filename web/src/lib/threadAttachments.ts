const SAFE_ARTIFACT_CHARS = /[^A-Za-z0-9._-]+/g;
const EDGE_PUNCTUATION = /^[.-]+|[.-]+$/g;
const SIZE_UNITS = ['B', 'KB', 'MB', 'GB'] as const;

export const MAX_THREAD_ATTACHMENTS = 5;
export const REMOVE_ATTACHMENT_LABEL = 'Remove attachment';

export function safeArtifactBasename(file: File): string {
  return file.name.replace(SAFE_ARTIFACT_CHARS, '-').replace(EDGE_PUNCTUATION, '') ||
    'attachment.bin';
}

export function safeArtifactName(prefix: string, file: File, collisionIndex = 1): string {
  const stamp = new Date().toISOString().replace(/[-:]/g, '').replace(/\.\d{3}Z$/, 'Z');
  const disambiguator = collisionIndex > 1 ? `${collisionIndex}-` : '';
  return `${prefix}-${stamp}-${disambiguator}${safeArtifactBasename(file)}`;
}

/**
 * Stable, non-metadata selection identity.
 *
 * The id is deliberately NOT derived from `File.name`/`size`/`lastModified`:
 * two distinct `File` objects with identical metadata (or literally the same
 * File selected twice) must stay distinct selections with distinct chip keys.
 * The returned factory is monotonic for its owner's lifetime, so a removed and
 * re-added file receives a fresh id (and therefore a fresh upload).
 */
export function createSelectionIdFactory(prefix = 'sel'): () => string {
  let counter = 0;
  return () => {
    counter += 1;
    return `${prefix}-${counter}`;
  };
}

/**
 * Reserve an artifact name for one selection, deterministically avoiding names
 * already reserved by this submission ({@link reserved}) or already allocated by
 * this page/dialog lifetime ({@link alreadyAllocated}).
 *
 * Reusing the same-second base name would make `ArtifactStore.put` replace a
 * previously uploaded artifact's bytes, conflating distinct selections. The
 * smallest free collision index is chosen; index 1 is the common-case format.
 */
export function allocateArtifactName(
  prefix: string,
  file: File,
  reserved: ReadonlySet<string>,
  alreadyAllocated: ReadonlySet<string> = new Set<string>(),
): string {
  for (let index = 1; index <= 1000; index += 1) {
    const candidate = safeArtifactName(prefix, file, index);
    if (!reserved.has(candidate) && !alreadyAllocated.has(candidate)) return candidate;
  }
  return safeArtifactName(prefix, file, 1001);
}

export function attachmentContentType(file: File): string | null {
  return file.type || null;
}

export function formatAttachmentSize(sizeBytes: number | null | undefined): string | null {
  if (sizeBytes === null || sizeBytes === undefined || !Number.isFinite(sizeBytes)) return null;
  if (sizeBytes < 0) return null;
  let value = sizeBytes;
  let unitIndex = 0;
  while (value >= 1024 && unitIndex < SIZE_UNITS.length - 1) {
    value /= 1024;
    unitIndex += 1;
  }
  const amount = unitIndex === 0 || value >= 10
    ? Math.round(value).toString()
    : value.toFixed(1).replace(/\.0$/, '');
  return `${amount} ${SIZE_UNITS[unitIndex]}`;
}
