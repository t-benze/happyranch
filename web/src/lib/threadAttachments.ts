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
