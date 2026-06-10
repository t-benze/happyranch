/**
 * Assets page — founder-facing browse + download + upload + delete for the
 * org-shared artifact store (`happyranch assets ...` on the CLI; delete is
 * web-only — there is no CLI delete verb).
 *
 * The daemon exposes POST (upload), GET (list), GET /{name} (download), and
 * DELETE /{name} (delete); there is intentionally NO update route (POST is an
 * idempotent create-or-overwrite). Uploads are validated client-side against
 * the same size/name constraints the daemon enforces so the founder sees an
 * inline error instead of a 400/413. Deletes require an explicit confirm.
 */
import { useId, useRef, useState } from 'react';
import { Download, Trash2, Upload } from 'lucide-react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { artifacts as artifactsApi, ApiError } from '@/lib/api';
import { useOrgSlug } from '@/lib/orgSlug';
import { formatAttachmentSize } from '@/lib/threadAttachments';
import { Button } from '@/design-system/primitives/Button';
import { Input } from '@/design-system/primitives/Input';
import { Label } from '@/design-system/primitives/Label';
import { EmptyState } from '@/design-system/patterns/EmptyState';
import { validateArtifactUpload } from './validation';

function describeUploadError(err: unknown): string {
  if (err instanceof ApiError) {
    if (err.code === 'artifact_too_large') return 'File exceeds the 10 MB limit.';
    if (err.code === 'invalid_artifact_name') {
      return 'Name may contain only letters, digits, dot, underscore, and hyphen.';
    }
    return `Upload failed (HTTP ${err.status}).`;
  }
  return String(err);
}

function describeDeleteError(err: unknown): string {
  if (err instanceof ApiError) {
    if (err.code === 'artifact_not_found') return 'That asset no longer exists.';
    return `Delete failed (HTTP ${err.status}).`;
  }
  return String(err);
}

export function AssetsPage(): JSX.Element {
  const slug = useOrgSlug();
  const qc = useQueryClient();
  const idBase = useId();
  const fileInputRef = useRef<HTMLInputElement>(null);
  const [file, setFile] = useState<File | null>(null);
  const [name, setName] = useState('');
  const [error, setError] = useState<string | null>(null);
  const [deleteError, setDeleteError] = useState<string | null>(null);

  const listQuery = useQuery({
    queryKey: ['artifacts', slug],
    queryFn: () => artifactsApi.listArtifacts(slug),
  });

  const upload = useMutation({
    mutationFn: (args: { file: File; name: string }) =>
      artifactsApi.uploadArtifact(slug, {
        file: args.file,
        name: args.name,
        agent: artifactsApi.ARTIFACT_WRITE_AGENT,
      }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['artifacts', slug] });
      setFile(null);
      setName('');
      setError(null);
      if (fileInputRef.current) fileInputRef.current.value = '';
    },
    onError: (err: unknown) => setError(describeUploadError(err)),
  });

  const del = useMutation({
    mutationFn: (artifactName: string) => artifactsApi.deleteArtifact(slug, artifactName),
    onSuccess: () => {
      setDeleteError(null);
      qc.invalidateQueries({ queryKey: ['artifacts', slug] });
    },
    onError: (err: unknown) => setDeleteError(describeDeleteError(err)),
  });

  const requestDelete = (artifactName: string) => {
    setDeleteError(null);
    // Never delete on a single unconfirmed click.
    if (!window.confirm(`Delete "${artifactName}"? This cannot be undone.`)) return;
    del.mutate(artifactName);
  };

  const submit = () => {
    setError(null);
    if (!file) {
      setError('Select a file to upload.');
      return;
    }
    // When no explicit name is given the daemon falls back to the file name,
    // so validate whichever name will actually be sent.
    const effectiveName = name.trim() || file.name;
    const validationError = validateArtifactUpload({
      name: effectiveName,
      sizeBytes: file.size,
    });
    if (validationError) {
      setError(validationError);
      return;
    }
    upload.mutate({ file, name: effectiveName });
  };

  const fileId = `${idBase}-file`;
  const nameId = `${idBase}-name`;
  const artifacts = listQuery.data?.artifacts ?? [];

  return (
    <div className="bg-surface-canvas h-full overflow-y-auto p-4">
      <div className="mx-auto flex max-w-3xl flex-col gap-6">
        <header>
          <h1 className="text-fg text-lg font-semibold">Shared artifacts</h1>
          <p className="text-fg-muted text-sm">
            Org-wide artifacts. Browse, download, upload a new file, or delete an
            existing one. Rename is not supported.
          </p>
        </header>

        <section
          aria-label="Upload asset"
          className="border-border bg-bg-subtle flex flex-col gap-3 rounded-md border p-4"
        >
          <h2 className="text-fg text-sm font-semibold">Upload asset</h2>
          <div className="flex flex-col gap-1">
            <Label htmlFor={fileId}>File</Label>
            <Input
              id={fileId}
              ref={fileInputRef}
              type="file"
              onChange={(e) => {
                setFile(e.target.files?.[0] ?? null);
                setError(null);
              }}
            />
          </div>
          <div className="flex flex-col gap-1">
            <Label htmlFor={nameId}>Name (optional — defaults to the file name)</Label>
            <Input
              id={nameId}
              type="text"
              value={name}
              placeholder="dev_agent-2026-06-10-report.pdf"
              onChange={(e) => {
                setName(e.target.value);
                setError(null);
              }}
            />
            <p className="text-fg-muted text-xs">
              Letters, digits, dot, underscore, hyphen. Max 200 characters, 10 MB.
            </p>
          </div>
          {error && (
            <p role="alert" className="text-feedback-danger text-sm">
              {error}
            </p>
          )}
          <div>
            <Button onClick={submit} disabled={upload.isPending}>
              <Upload aria-hidden="true" />
              {upload.isPending ? 'Uploading…' : 'Upload'}
            </Button>
          </div>
        </section>

        <section aria-label="Artifacts list">
          {listQuery.isLoading ? (
            <p className="text-fg-muted">Loading…</p>
          ) : listQuery.isError ? (
            <p role="alert" className="text-feedback-danger text-sm">
              Could not load assets.
            </p>
          ) : artifacts.length === 0 ? (
            <EmptyState
              title="No artifacts"
              body="Upload a file above to share it across the org."
            />
          ) : (
            <>
              {deleteError && (
                <p role="alert" className="text-feedback-danger mb-2 text-sm">
                  {deleteError}
                </p>
              )}
              <table className="w-full text-sm">
                <thead>
                  <tr className="text-fg-muted border-border border-b text-left">
                    <th className="py-2 pr-4 font-medium">Name</th>
                    <th className="py-2 pr-4 font-medium">Size</th>
                    <th className="py-2 pr-4 font-medium">Modified</th>
                    <th className="py-2 font-medium">
                      <span className="sr-only">Actions</span>
                    </th>
                  </tr>
                </thead>
                <tbody>
                  {artifacts.map((a) => (
                    <tr key={a.name} className="border-border/50 border-b">
                      <td className="text-fg py-2 pr-4 break-all">{a.name}</td>
                      <td className="text-fg-muted py-2 pr-4 whitespace-nowrap">
                        {formatAttachmentSize(a.size_bytes) ?? '—'}
                      </td>
                      <td className="text-fg-muted py-2 pr-4 whitespace-nowrap">
                        {a.modified_at}
                      </td>
                      <td className="py-2">
                        <div className="flex items-center gap-4 whitespace-nowrap">
                          <a
                            href={artifactsApi.artifactDownloadPath(slug, a.name)}
                            download={a.name}
                            className="text-accent inline-flex items-center gap-1 hover:underline"
                          >
                            <Download size={14} aria-hidden="true" />
                            Download
                          </a>
                          <button
                            type="button"
                            onClick={() => requestDelete(a.name)}
                            disabled={del.isPending && del.variables === a.name}
                            aria-label={`Delete ${a.name}`}
                            className="text-feedback-danger inline-flex items-center gap-1 hover:underline disabled:opacity-50"
                          >
                            <Trash2 size={14} aria-hidden="true" />
                            {del.isPending && del.variables === a.name ? 'Deleting…' : 'Delete'}
                          </button>
                        </div>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </>
          )}
        </section>
      </div>
    </div>
  );
}
