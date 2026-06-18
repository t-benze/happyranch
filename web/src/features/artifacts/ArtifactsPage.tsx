/**
 * Artifacts page — flat 3-column card grid (§4.6 PRD final).
 *
 * The daemon artifact route returns only `name`, `size_bytes`, and
 * `modified_at` — no agent, task_id, thread, kind/type, or dream_id fields.
 * Per the honesty lens (P1), cards show ONLY stored fields:
 *   - name (artifact file name)
 *   - size (formatted: "5 MB", etc.)
 *   - modified_at (formatted timestamp)
 *   - download action (wired to existing `GET /artifacts/{name}` route)
 *   - delete action (wired to existing `DELETE /artifacts/{name}` route)
 *
 * No kind pill, status tag, provenance, IdBadge, PR/CI panel, or dream
 * marker — none of those fields exist on the stored artifact record.
 * Upload remains available (existing feature, kept per reshape directive).
 *
 * States: loading skeleton, calm empty ("No artifacts yet"), error with retry.
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
import { PageHeader } from '@/design-system/patterns/PageHeader';
import { EmptyState } from '@/design-system/patterns/EmptyState';
import { validateArtifactUpload } from './validation';

/* ------------------------------------------------------------------ */
/*  Error helpers                                                      */
/* ------------------------------------------------------------------ */

function describeUploadError(err: unknown): string {
  if (err instanceof ApiError) {
    if (err.code === 'artifact_too_large') return 'File exceeds the 10 MB limit.';
    if (err.code === 'invalid_artifact_name') {
      return 'Name: each segment (between slashes) must match [A-Za-z0-9._-]+; forward slash only as separator (no leading/trailing/empty segments). Max 200 characters, 10 MB file cap.';
    }
    return `Upload failed (HTTP ${err.status}).`;
  }
  return String(err);
}

function describeDeleteError(err: unknown): string {
  if (err instanceof ApiError) {
    if (err.code === 'artifact_not_found') return 'That artifact no longer exists.';
    return `Delete failed (HTTP ${err.status}).`;
  }
  return String(err);
}

function formatModifiedAt(iso: string): string {
  return new Date(iso).toLocaleString(undefined, {
    year: 'numeric',
    month: 'short',
    day: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
  });
}

/* ------------------------------------------------------------------ */
/*  Skeleton                                                           */
/* ------------------------------------------------------------------ */

function ArtifactsSkeleton(): JSX.Element {
  return (
    <div
      className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3"
      aria-label="Loading artifacts"
    >
      {[1, 2, 3, 4, 5, 6].map((i) => (
        <div
          key={i}
          className="border-border-subtle bg-surface-canvas rounded-lg border p-4"
        >
          <div className="bg-surface-sunken mb-2 h-4 w-3/4 animate-pulse rounded" />
          <div className="bg-surface-sunken mb-3 h-3 w-1/3 animate-pulse rounded" />
          <div className="bg-surface-sunken h-3 w-1/2 animate-pulse rounded" />
        </div>
      ))}
    </div>
  );
}

/* ------------------------------------------------------------------ */
/*  Artifact card                                                      */
/* ------------------------------------------------------------------ */

interface ArtifactCardProps {
  name: string;
  sizeBytes: number;
  modifiedAt: string;
  onDownload: (name: string) => void;
  onDelete: (name: string) => void;
  isDeleting: boolean;
}

function ArtifactCard({
  name,
  sizeBytes,
  modifiedAt,
  onDownload,
  onDelete,
  isDeleting,
}: ArtifactCardProps): JSX.Element {
  const size = formatAttachmentSize(sizeBytes) ?? '—';

  return (
    <div className="border-border-subtle bg-surface-canvas hover:bg-surface-raised flex flex-col rounded-lg border p-4 transition-colors">
      {/* File name */}
      <h3
        className="text-fg mb-1 text-sm font-medium break-all"
        title={name}
      >
        {name}
      </h3>

      {/* Size + modified */}
      <div className="text-fg-muted mb-3 flex flex-wrap items-center gap-x-3 gap-y-0.5 text-xs">
        <span>{size}</span>
        <span>{formatModifiedAt(modifiedAt)}</span>
      </div>

      {/* Actions */}
      <div className="mt-auto flex items-center gap-3">
        <button
          type="button"
          onClick={() => onDownload(name)}
          className="text-accent inline-flex items-center gap-1 text-xs hover:underline"
        >
          <Download size={14} aria-hidden="true" />
          Download
        </button>
        <button
          type="button"
          onClick={() => onDelete(name)}
          disabled={isDeleting}
          aria-label={`Delete ${name}`}
          className="text-feedback-danger inline-flex items-center gap-1 text-xs hover:underline disabled:opacity-50"
        >
          <Trash2 size={14} aria-hidden="true" />
          {isDeleting ? 'Deleting…' : 'Delete'}
        </button>
      </div>
    </div>
  );
}

/* ------------------------------------------------------------------ */
/*  Main component                                                     */
/* ------------------------------------------------------------------ */

export function ArtifactsPage(): JSX.Element {
  const slug = useOrgSlug();
  const qc = useQueryClient();
  const idBase = useId();
  const fileInputRef = useRef<HTMLInputElement>(null);
  const [file, setFile] = useState<File | null>(null);
  const [name, setName] = useState('');
  const [error, setError] = useState<string | null>(null);
  const [deleteError, setDeleteError] = useState<string | null>(null);
  const [downloadError, setDownloadError] = useState<string | null>(null);
  const [showUpload, setShowUpload] = useState(false);

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
      setShowUpload(false);
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
    if (!window.confirm(`Delete "${artifactName}"? This cannot be undone.`)) return;
    del.mutate(artifactName);
  };

  const submit = () => {
    setError(null);
    if (!file) {
      setError('Select a file to upload.');
      return;
    }
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
    <div className="bg-surface-canvas flex h-full flex-col">
      {/* Header */}
      <header className="border-border-subtle border-b p-4">
        <div className="flex items-start justify-between gap-3">
          <PageHeader
            title="Artifacts"
            meta="Org-wide artifacts. Browse, download, upload, or delete."
          />
          <Button
            variant="secondary"
            size="sm"
            onClick={() => setShowUpload((v) => !v)}
          >
            <Upload aria-hidden="true" size={14} />
            {showUpload ? 'Cancel' : 'Upload'}
          </Button>
        </div>

        {/* Upload form (collapsible) */}
        {showUpload && (
          <section
            aria-label="Upload artifact"
            className="border-border bg-surface-sunken mt-4 flex flex-col gap-3 rounded-md border p-4"
          >
            <h3 className="text-fg text-sm font-semibold">Upload artifact</h3>
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
                Each '/'-separated segment must match [A-Za-z0-9._-]+ (letters, digits, dot, underscore, hyphen). Forward slash only as separator; no leading/trailing/empty segments. Max 200 characters, 10 MB.
              </p>
            </div>
            {error && (
              <p role="alert" className="text-feedback-danger text-sm">
                {error}
              </p>
            )}
            <div>
              <Button onClick={submit} disabled={upload.isPending}>
                <Upload aria-hidden="true" size={14} />
                {upload.isPending ? 'Uploading…' : 'Upload'}
              </Button>
            </div>
          </section>
        )}
      </header>

      {/* Main content */}
      <div className="flex-1 overflow-y-auto p-4">
        {/* Loading */}
        {listQuery.isLoading && <ArtifactsSkeleton />}

        {/* Error */}
        {listQuery.isError && (
          <div className="flex flex-col items-center justify-center gap-3 p-8 text-center">
            <p className="text-tier-red text-sm">
              Could not load artifacts.
              {listQuery.error?.message && <> {listQuery.error.message}</>}
            </p>
            <Button
              size="sm"
              variant="outline"
              onClick={() =>
                qc.invalidateQueries({ queryKey: ['artifacts', slug] })
              }
            >
              Retry
            </Button>
          </div>
        )}

        {/* Empty */}
        {!listQuery.isLoading && !listQuery.isError && artifacts.length === 0 && (
          <div className="flex h-full items-center justify-center">
            <EmptyState
              title="No artifacts yet"
              body="Upload a file above to share it across the org."
            />
          </div>
        )}

        {/* Card grid */}
        {!listQuery.isLoading && !listQuery.isError && artifacts.length > 0 && (
          <>
            {/* Banner for delete/download errors */}
            {deleteError && (
              <p role="alert" className="text-feedback-danger mb-4 text-sm">
                {deleteError}
              </p>
            )}
            {downloadError && !deleteError && (
              <p role="alert" className="text-feedback-danger mb-4 text-sm">
                {downloadError}
              </p>
            )}

            <div
              aria-label="Artifacts list"
              className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3"
            >
              {artifacts.map((a) => (
                <ArtifactCard
                  key={a.name}
                  name={a.name}
                  sizeBytes={a.size_bytes}
                  modifiedAt={a.modified_at}
                  onDownload={(artifactName) => {
                    setDownloadError(null);
                    artifactsApi
                      .downloadArtifact(slug, artifactName)
                      .catch((err: unknown) => {
                        const msg =
                          err instanceof ApiError
                            ? `Download failed (HTTP ${err.status}).`
                            : String(err);
                        setDownloadError(msg);
                      });
                  }}
                  onDelete={requestDelete}
                  isDeleting={del.isPending && del.variables === a.name}
                />
              ))}
            </div>
          </>
        )}
      </div>
    </div>
  );
}
