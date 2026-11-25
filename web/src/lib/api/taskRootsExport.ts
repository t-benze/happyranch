/**
 * GET /orgs/{slug}/tasks/roots/export (THR-390 PR5 route, PR8 client).
 *
 * One bounded page of root tasks with current attribute values, as JSON or
 * CSV. The response is a file, so this uses the same bearer + fenced-Blob flow
 * as `downloadArtifact`: send once and, on 401, clear the cached bearer,
 * re-acquire and send once more. No other auth behavior is added.
 */
import { clearToken } from '../auth';
import { API_PREFIX, ApiError, fencedText, pinTarget, pinnedToken, schemeFetch, staleOutcome, type Fenced } from './client';
import { isStaleTarget } from '../native/target';
import { encodeQuery, type AttributeFilter } from './taskRootsFilters';

export type TaskRootsExportFormat = 'json' | 'csv';

export interface TaskRootsExportParams {
  format: TaskRootsExportFormat;
  limit: number;
  status?: string;
  assigned_agent?: string;
  attributes: readonly AttributeFilter[];
  before?: string;
}

export interface TaskRootsExportResult {
  blob: Blob;
  /** Content-Disposition filename (filename* preferred); null when absent. */
  filename: string | null;
  limit: number | null;
  pageFull: boolean;
  nextCursor: string | null;
}

/** Exact request query: format, limit, status, agent, attr_* (in order), before. */
export function exportQuery(params: TaskRootsExportParams): string {
  const pairs: [string, string][] = [['format', params.format], ['limit', String(params.limit)]];
  if (params.status) pairs.push(['status', params.status]);
  if (params.assigned_agent) pairs.push(['assigned_agent', params.assigned_agent]);
  for (const [name, value] of params.attributes) pairs.push([name, value]);
  if (params.before) pairs.push(['before', params.before]);
  return encodeQuery(pairs);
}

/** Last path segment only, without separators or dot-only names. */
function safeName(raw: string): string | null {
  const base = raw.split(/[\\/]/).pop()?.trim() ?? '';
  if (!base || /^\.+$/.test(base)) return null;
  return base;
}

/** RFC 6266/5987: prefer `filename*=UTF-8''…`, then `filename="…"`/token. */
export function filenameFromContentDisposition(header: string | null): string | null {
  if (!header) return null;
  const star = /filename\*\s*=\s*([^']*)'[^']*'([^;]+)/i.exec(header);
  if (star) {
    try {
      const decoded = safeName(decodeURIComponent(star[2].trim().replace(/^"|"$/g, '')));
      if (decoded) return decoded;
    } catch {
      // fall through to the plain parameter
    }
  }
  const quoted = /filename\s*=\s*"((?:[^"\\]|\\.)*)"/i.exec(header);
  if (quoted) return safeName(quoted[1].replace(/\\(.)/g, '$1'));
  const token = /filename\s*=\s*([^;\s]+)/i.exec(header);
  return token ? safeName(token[1]) : null;
}

async function exportBlob(fenced: Fenced): Promise<Blob> {
  let blob: Blob;
  try {
    blob = await fenced.res.blob();
  } catch (error) {
    if (isStaleTarget(fenced.captured)) throw staleOutcome(true);
    throw error;
  }
  if (isStaleTarget(fenced.captured)) throw staleOutcome(true);
  return blob;
}

function send(path: string, token: string, pinned: string, signal?: AbortSignal): Promise<Fenced> {
  return schemeFetch(path, {
    method: 'GET',
    headers: { Authorization: `Bearer ${token}` },
    credentials: 'same-origin',
    signal,
  }, pinned);
}

export async function exportTaskRoots(
  slug: string,
  params: TaskRootsExportParams,
  signal?: AbortSignal,
): Promise<TaskRootsExportResult> {
  const path = `${API_PREFIX}/orgs/${slug}/tasks/roots/export?${exportQuery(params)}`;
  const pinned = await pinTarget(true);
  let token = await pinnedToken(true);
  let fenced = await send(path, token, pinned, signal);
  if (fenced.res.status === 401) {
    clearToken();
    token = await pinnedToken(true);
    fenced = await send(path, token, fenced.captured, signal);
  }
  if (!fenced.res.ok) {
    let body: unknown = null;
    const text = await fencedText(fenced, true);
    if (text) {
      try { body = JSON.parse(text); } catch { body = text; }
    }
    const detail = body && typeof body === 'object' && 'detail' in body ? (body as { detail: unknown }).detail : body;
    const code = detail && typeof detail === 'object' && typeof (detail as { code?: unknown }).code === 'string'
      ? (detail as { code: string }).code : null;
    throw new ApiError(fenced.res.status, code, detail);
  }
  const headers = fenced.res.headers;
  const blob = await exportBlob(fenced);
  const limit = Number.parseInt(headers.get('X-HappyRanch-Export-Limit') ?? '', 10);
  return {
    blob,
    filename: filenameFromContentDisposition(headers.get('Content-Disposition')),
    limit: Number.isFinite(limit) ? limit : null,
    pageFull: (headers.get('X-HappyRanch-Page-Full') ?? '').toLowerCase() === 'true',
    nextCursor: headers.get('X-HappyRanch-Next-Cursor') || null,
  };
}
