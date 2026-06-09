import { clearToken, getToken } from '../auth';
import { API_PREFIX, ApiError } from './client';

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

export function artifactDownloadPath(slug: string, artifactName: string): string {
  return `${API_PREFIX}/orgs/${slug}/artifacts/${encodeURIComponent(artifactName)}`;
}
