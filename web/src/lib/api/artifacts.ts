import { clearToken, getToken } from '../auth';
import { API_PREFIX, ApiError } from './client';

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

function parseArtifactError(status: number, body: unknown): ApiError {
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
): Promise<Response> {
  const params = new URLSearchParams({ agent: args.agent });
  if (args.name) params.set('name', args.name);
  const form = new FormData();
  form.set('file', args.file, args.name ?? args.file.name);
  return fetch(`${API_PREFIX}/orgs/${slug}/artifacts?${params.toString()}`, {
    method: 'POST',
    headers: {
      Authorization: `Bearer ${token}`,
      Accept: 'application/json',
    },
    body: form,
    credentials: 'same-origin',
  });
}

export async function uploadArtifact(
  slug: string,
  args: UploadArtifactArgs,
): Promise<ArtifactInfo> {
  let token = await getToken();
  let res = await uploadWithToken(slug, args, token);
  if (res.status === 401) {
    clearToken();
    token = await getToken();
    res = await uploadWithToken(slug, args, token);
  }

  let body: unknown = null;
  const text = await res.text();
  if (text) {
    try {
      body = JSON.parse(text);
    } catch {
      body = text;
    }
  }
  if (!res.ok) throw parseArtifactError(res.status, body);
  return body as ArtifactInfo;
}

async function listWithToken(slug: string, token: string): Promise<Response> {
  return fetch(`${API_PREFIX}/orgs/${slug}/artifacts`, {
    method: 'GET',
    headers: {
      Authorization: `Bearer ${token}`,
      Accept: 'application/json',
    },
    credentials: 'same-origin',
  });
}

export async function listArtifacts(slug: string): Promise<ListArtifactsResponse> {
  let token = await getToken();
  let res = await listWithToken(slug, token);
  if (res.status === 401) {
    clearToken();
    token = await getToken();
    res = await listWithToken(slug, token);
  }

  let body: unknown = null;
  const text = await res.text();
  if (text) {
    try {
      body = JSON.parse(text);
    } catch {
      body = text;
    }
  }
  if (!res.ok) throw parseArtifactError(res.status, body);
  return body as ListArtifactsResponse;
}

export function artifactDownloadPath(slug: string, artifactName: string): string {
  return `${API_PREFIX}/orgs/${slug}/artifacts/${encodeURIComponent(artifactName)}`;
}

async function downloadWithToken(
  slug: string,
  name: string,
  token: string,
): Promise<Response> {
  return fetch(
    `${API_PREFIX}/orgs/${slug}/artifacts/${encodeURIComponent(name)}`,
    {
      method: 'GET',
      headers: {
        Authorization: `Bearer ${token}`,
      },
      credentials: 'same-origin',
    },
  );
}

/**
 * Download an artifact by fetching it with the bearer token (so the daemon
 * authenticates the request) and then triggering a programmatic browser
 * download via an object URL.  Errors are surfaced as ApiError so the UI can
 * show an inline message instead of the browser-native “Needs authorisation”
 * text.
 */
export async function downloadArtifact(slug: string, name: string): Promise<void> {
  let token = await getToken();
  let res = await downloadWithToken(slug, name, token);
  if (res.status === 401) {
    clearToken();
    token = await getToken();
    res = await downloadWithToken(slug, name, token);
  }

  if (!res.ok) {
    let body: unknown = null;
    const text = await res.text();
    if (text) {
      try {
        body = JSON.parse(text);
      } catch {
        body = text;
      }
    }
    throw parseArtifactError(res.status, body);
  }

  const blob = await res.blob();
  const objectUrl = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = objectUrl;
  a.download = name;
  a.click();
  URL.revokeObjectURL(objectUrl);
}

async function deleteWithToken(slug: string, name: string, token: string): Promise<Response> {
  const params = new URLSearchParams({ agent: ARTIFACT_WRITE_AGENT });
  return fetch(
    `${API_PREFIX}/orgs/${slug}/artifacts/${encodeURIComponent(name)}?${params.toString()}`,
    {
      method: 'DELETE',
      headers: {
        Authorization: `Bearer ${token}`,
        Accept: 'application/json',
      },
      credentials: 'same-origin',
    },
  );
}

export async function deleteArtifact(slug: string, name: string): Promise<void> {
  let token = await getToken();
  let res = await deleteWithToken(slug, name, token);
  if (res.status === 401) {
    clearToken();
    token = await getToken();
    res = await deleteWithToken(slug, name, token);
  }

  if (!res.ok) {
    let body: unknown = null;
    const text = await res.text();
    if (text) {
      try {
        body = JSON.parse(text);
      } catch {
        body = text;
      }
    }
    throw parseArtifactError(res.status, body);
  }
}
