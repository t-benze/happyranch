import { clearToken, getToken } from '../auth';
import {
  API_PREFIX,
  ApiError,
  fencedText,
  nativeRecoveryErrorFor,
  pinTarget,
  schemeFetch,
  staleOutcome,
  type Fenced,
} from './client';
import { isStaleTarget } from '../native/target';
import { MAX_UPLOAD_FILE_BYTES, prepareMultipartUpload } from './multipart';

/**
 * The agent identity attributed to browser-driven artifact writes (upload +
 * delete) for audit. Founder is the only actor behind the web artifacts UI; both
 * write paths reuse this single source so the audit attribution stays
 * consistent.
 */
export const ARTIFACT_WRITE_AGENT = 'founder';

export interface ArtifactInfo {
  name: string;
  size_bytes: number;
  modified_at: string;
}

export interface UploadArtifactArgs {
  file: File;
  name?: string;
  agent: string;
}

export interface ListArtifactsResponse {
  artifacts: ArtifactInfo[];
}

/** Read a fenced download body; a read finishing after a switch is stale. */
async function fencedBlob(fenced: Fenced): Promise<Blob> {
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

function parseArtifactError(status: number, body: unknown): ApiError {
  const recovery = nativeRecoveryErrorFor(status, body);
  if (recovery) return recovery;
  let code: string | null = null;
  let detail: unknown = body;
  if (
    body &&
    typeof body === 'object' &&
    'detail' in body &&
    (body as { detail: unknown }).detail !== undefined
  ) {
    detail = (body as { detail: unknown }).detail;
    if (
      detail &&
      typeof detail === 'object' &&
      'code' in detail &&
      typeof (detail as { code?: unknown }).code === 'string'
    ) {
      code = (detail as { code: string }).code;
    }
  }
  return new ApiError(status, code, detail);
}

async function uploadWithToken(
  slug: string,
  args: UploadArtifactArgs,
  token: string,
  pinned: string,
): Promise<Fenced> {
  const params = new URLSearchParams({ agent: args.agent });
  if (args.name) params.set('name', args.name);
  const form = new FormData();
  form.set('file', args.file, args.name ?? args.file.name);
  const upload = await prepareMultipartUpload(
    form,
    new ApiError(413, 'artifact_too_large', {
      code: 'artifact_too_large',
      max_bytes: MAX_UPLOAD_FILE_BYTES,
      size_bytes: args.file.size,
    }),
  );
  return schemeFetch(`${API_PREFIX}/orgs/${slug}/artifacts?${params.toString()}`, {
    method: 'POST',
    headers: {
      Authorization: `Bearer ${token}`,
      Accept: 'application/json',
      ...upload.headers,
    },
    body: upload.body,
    credentials: 'same-origin',
  }, pinned);
}

export async function uploadArtifact(
  slug: string,
  args: UploadArtifactArgs,
): Promise<ArtifactInfo> {
  // F1: one generation per logical operation, pinned before any preparatory
  // await and kept across the 401 re-send (a change never moves targets).
  const pinned = await pinTarget(false);
  let token = await getToken();
  let fenced = await uploadWithToken(slug, args, token, pinned);
  if (fenced.res.status === 401) {
    clearToken();
    token = await getToken();
    fenced = await uploadWithToken(slug, args, token, fenced.captured);
  }

  let body: unknown = null;
  const text = await fencedText(fenced, false);
  if (text) {
    try {
      body = JSON.parse(text);
    } catch {
      body = text;
    }
  }
  if (!fenced.res.ok) throw parseArtifactError(fenced.res.status, body);
  return body as ArtifactInfo;
}

async function listWithToken(slug: string, token: string, pinned: string): Promise<Fenced> {
  return schemeFetch(`${API_PREFIX}/orgs/${slug}/artifacts`, {
    method: 'GET',
    headers: {
      Authorization: `Bearer ${token}`,
      Accept: 'application/json',
    },
    credentials: 'same-origin',
  }, pinned);
}

export async function listArtifacts(slug: string): Promise<ListArtifactsResponse> {
  const pinned = await pinTarget(true);
  let token = await getToken();
  let fenced = await listWithToken(slug, token, pinned);
  if (fenced.res.status === 401) {
    clearToken();
    token = await getToken();
    fenced = await listWithToken(slug, token, fenced.captured);
  }

  let body: unknown = null;
  const text = await fencedText(fenced, true);
  if (text) {
    try {
      body = JSON.parse(text);
    } catch {
      body = text;
    }
  }
  if (!fenced.res.ok) throw parseArtifactError(fenced.res.status, body);
  return body as ListArtifactsResponse;
}

export function artifactDownloadPath(slug: string, artifactName: string): string {
  return `${API_PREFIX}/orgs/${slug}/artifacts/${encodeURIComponent(artifactName)}`;
}

async function downloadWithToken(path: string, token: string, pinned: string): Promise<Fenced> {
  return schemeFetch(path, {
    method: 'GET',
    headers: {
      Authorization: `Bearer ${token}`,
    },
    credentials: 'same-origin',
  }, pinned);
}

/**
 * Download an artifact by fetching it with the bearer token (so the daemon
 * authenticates the request) and then triggering a programmatic browser
 * download via an object URL.  Errors are surfaced as ApiError so the UI can
 * show an inline message instead of the browser-native “Needs authorisation”
 * text.
 */
export async function downloadArtifact(slug: string, name: string): Promise<void> {
  const pinned = await pinTarget(true);
  let token = await getToken();
  const path = `${API_PREFIX}/orgs/${slug}/artifacts/${encodeURIComponent(name)}`;
  let fenced = await downloadWithToken(path, token, pinned);
  if (fenced.res.status === 401) {
    clearToken();
    token = await getToken();
    fenced = await downloadWithToken(path, token, fenced.captured);
  }

  if (!fenced.res.ok) {
    let body: unknown = null;
    const text = await fencedText(fenced, true);
    if (text) {
      try {
        body = JSON.parse(text);
      } catch {
        body = text;
      }
    }
    throw parseArtifactError(fenced.res.status, body);
  }

  const blob = await fencedBlob(fenced);
  const objectUrl = URL.createObjectURL(blob);
  try {
    const a = document.createElement('a');
    a.href = objectUrl;
    a.download = name;
    a.click();
  } finally {
    URL.revokeObjectURL(objectUrl);
  }
}

/**
 * Download a thread-scoped attachment via the participation-gated route
 * GET /api/v1/orgs/{slug}/threads/{threadId}/attachments/{attachmentId}.
 * Uses the identical bearer-token + retry-on-401 flow as downloadArtifact.
 */
export async function downloadThreadAttachment(
  slug: string,
  threadId: string,
  attachmentId: string,
  displayName: string,
): Promise<void> {
  const pinned = await pinTarget(true);
  let token = await getToken();
  const params = new URLSearchParams({ agent: ARTIFACT_WRITE_AGENT });
  const path = `${API_PREFIX}/orgs/${slug}/threads/${threadId}/attachments/${attachmentId}?${params.toString()}`;
  let fenced = await downloadWithToken(path, token, pinned);
  if (fenced.res.status === 401) {
    clearToken();
    token = await getToken();
    fenced = await downloadWithToken(path, token, fenced.captured);
  }

  if (!fenced.res.ok) {
    let body: unknown = null;
    const text = await fencedText(fenced, true);
    if (text) {
      try {
        body = JSON.parse(text);
      } catch {
        body = text;
      }
    }
    throw parseArtifactError(fenced.res.status, body);
  }

  const blob = await fencedBlob(fenced);
  const objectUrl = URL.createObjectURL(blob);
  try {
    const a = document.createElement('a');
    a.href = objectUrl;
    a.download = displayName;
    a.click();
  } finally {
    URL.revokeObjectURL(objectUrl);
  }
}

async function deleteWithToken(slug: string, name: string, token: string, pinned: string): Promise<Fenced> {
  const params = new URLSearchParams({ agent: ARTIFACT_WRITE_AGENT });
  return schemeFetch(
    `${API_PREFIX}/orgs/${slug}/artifacts/${encodeURIComponent(name)}?${params.toString()}`,
    {
      method: 'DELETE',
      headers: {
        Authorization: `Bearer ${token}`,
        Accept: 'application/json',
      },
      credentials: 'same-origin',
    },
    pinned,
  );
}

export async function deleteArtifact(slug: string, name: string): Promise<void> {
  const pinned = await pinTarget(false);
  let token = await getToken();
  let fenced = await deleteWithToken(slug, name, token, pinned);
  if (fenced.res.status === 401) {
    clearToken();
    token = await getToken();
    fenced = await deleteWithToken(slug, name, token, fenced.captured);
  }

  if (!fenced.res.ok) {
    let body: unknown = null;
    const text = await fencedText(fenced, false);
    if (text) {
      try {
        body = JSON.parse(text);
      } catch {
        body = text;
      }
    }
    throw parseArtifactError(fenced.res.status, body);
  }
}
