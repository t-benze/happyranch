import type { MessageKey, MessageParams } from '@/lib/i18n';

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
 * Validate an upload before it is sent. Returns a locale-neutral error
 * descriptor, or `null` when the upload is acceptable.
 */
export function validateArtifactUpload(input: {
  name: string;
  sizeBytes: number;
}): ArtifactErrorView | null {
  const { name, sizeBytes } = input;
  if (!name) return { kind: 'message', key: 'artifacts.error.requiredName' };
  if (name.length > MAX_ARTIFACT_NAME_LENGTH) {
    return { kind: 'message', key: 'artifacts.error.longName', params: { count: MAX_ARTIFACT_NAME_LENGTH, limit: MAX_ARTIFACT_NAME_LENGTH } };
  }
  if (name.startsWith('/') || name.endsWith('/') || name.includes('//') || name.includes('\\')) {
    return { kind: 'message', key: 'artifacts.error.invalidName' };
  }
  for (const seg of name.split('/')) {
    if (!seg || seg === '..' || seg.startsWith('.')) {
      return { kind: 'message', key: 'artifacts.error.invalidName' };
    }
    if (!ARTIFACT_NAME_RE.test(seg)) {
      return { kind: 'message', key: 'artifacts.error.invalidName' };
    }
  }
  if (sizeBytes > MAX_ARTIFACT_BYTES) {
    return { kind: 'message', key: 'artifacts.error.tooLarge' };
  }
  return null;
}

export type ArtifactErrorView = { kind: 'message'; key: MessageKey; params?: MessageParams } | { kind: 'raw'; text: string };
const ERROR_KEYS: Record<string, MessageKey> = {
  artifact_too_large: 'artifacts.error.tooLarge', invalid_artifact_name: 'artifacts.error.daemonName',
  artifact_not_found: 'artifacts.error.notFound',
};
/** Shipped F1 diagnostic boundary, without ApiError/data-layer coupling. */
export function classifyArtifactError(err: unknown, fallback: MessageKey): ArtifactErrorView {
  const code = (err as { code?: string } | null | undefined)?.code;
  if (code) return Object.prototype.hasOwnProperty.call(ERROR_KEYS, code)
    ? { kind: 'message', key: ERROR_KEYS[code] } : { kind: 'raw', text: code };
  let text: unknown = null;
  if (typeof err === 'string') text = err;
  else if (err !== null && typeof err === 'object' && 'detail' in err) text = err.detail;
  else if (err instanceof Error) text = err.message;
  return typeof text === 'string' && text.trim() !== ''
    ? { kind: 'raw', text } : { kind: 'message', key: fallback };
}
export function renderArtifactError(view: ArtifactErrorView, t: (key: MessageKey, params?: MessageParams) => string): string {
  return view.kind === 'raw' ? view.text : t(view.key, view.params);
}
