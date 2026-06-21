/**
 * Artifacts page — "produced-artifacts" recency card grid (THR-030 ART-01..04).
 *
 * The daemon artifact route returns only `name`, `size_bytes`, and
 * `modified_at` — no stored type, status, agent, thread, or authored-at.
 * Per the honesty fence, everything richer than those three fields is DERIVED
 * client-side from the file name (see ./artifact-meta):
 *   - type pill + centered type icon, from the extension / a PR token (ART-01)
 *   - provenance line "THR · agent · date", parsed from the
 *     `<agent>-<YYYY-MM-DD>-<slug>` convention; neutral when it doesn't match
 *   - eyebrow "N ARTIFACTS · PRODUCED BY N THREADS" + serif title (ART-03)
 *   - segmented type filter + "Recent first" sort (ART-02)
 *
 * DELIBERATELY NOT rendered (no data source — deferred to a backend change):
 *   - the authoritative status pill (merged/draft/open/final/applied)
 *   - server-captured provenance (real agent/thread/time at put-time)
 * We never fabricate either.
 *
 * ART-04: this is presented as a read view of what the org produced. Download
 * stays the primary card action (wiring unchanged); Delete is de-emphasised to
 * an icon affordance and Upload to a secondary header toggle — the raw
 * file-manager chrome is no longer the primary vocabulary.
 *
 * Recency: the list is sorted by `modified_at` (the real, always-present server
 * mtime) descending — a stronger "Recent first" signal than the name-embedded
 * date, and ISO-8601 "Z" strings sort lexicographically = chronologically.
 */
import { useId, useMemo, useRef, useState } from 'react';
import { Download, File, FileDiff, FileText, GitPullRequest, Image, Trash2, Upload } from 'lucide-react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { artifacts as artifactsApi, ApiError } from '@/lib/api';
import { useOrgSlug } from '@/lib/orgSlug';
import { formatAttachmentSize } from '@/lib/threadAttachments';
import { Button } from '@/design-system/primitives/Button';
import { Input } from '@/design-system/primitives/Input';
import { Label } from '@/design-system/primitives/Label';
import { EmptyState } from '@/design-system/patterns/EmptyState';
import { IdBadge } from '@/design-system/patterns/IdBadge';
import {
  deriveArtifactType,
  deriveTitle,
  formatProvenanceDate,
  parseProvenance,
  type ArtifactType,
} from './artifact-meta';
import { validateArtifactUpload } from './validation';

/* ------------------------------------------------------------------ */
/*  Type → presentation (pill label, centered icon, icon tint)         */
/* ------------------------------------------------------------------ */

const TYPE_META: Record<
  ArtifactType,
  { pill: string; Icon: typeof File; tint: string }
> = {
  'pull-request': { pill: 'pull request', Icon: GitPullRequest, tint: 'text-accent-text' },
  doc: { pill: 'document', Icon: FileText, tint: 'text-text-secondary' },
  patch: { pill: 'patch', Icon: FileDiff, tint: 'text-accent-text' },
  design: { pill: 'design', Icon: Image, tint: 'text-text-secondary' },
  file: { pill: 'file', Icon: File, tint: 'text-text-muted' },
};

/** Segmented filter — "All" plus the four named categories ("file" lives in All). */
const FILTERS: { key: ArtifactType | 'all'; label: string }[] = [
  { key: 'all', label: 'All' },
  { key: 'pull-request', label: 'Pull requests' },
  { key: 'doc', label: 'Docs' },
  { key: 'patch', label: 'Patches' },
  { key: 'design', label: 'Designs' },
];

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
          className="bg-surface border-border-default overflow-hidden rounded-lg border"
        >
          <div className="bg-surface-sunken h-28 animate-pulse" />
          <div className="flex flex-col gap-2 p-4">
            <div className="bg-surface-sunken h-4 w-3/4 animate-pulse rounded" />
            <div className="bg-surface-sunken h-3 w-1/2 animate-pulse rounded" />
          </div>
        </div>
      ))}
    </div>
  );
}

/* ------------------------------------------------------------------ */
/*  Thumbnail header — hatched backdrop + type pill + centered icon    */
/* ------------------------------------------------------------------ */

function ThumbnailHeader({ type }: { type: ArtifactType }): JSX.Element {
  const patternId = useId();
  const { pill, Icon, tint } = TYPE_META[type];
  return (
    <div className="bg-surface-sunken border-border-subtle relative flex h-28 items-center justify-center overflow-hidden border-b">
      {/* Diagonal hatch — SVG (no inline style / arbitrary Tailwind, per LRN-037). */}
      <svg aria-hidden="true" className="text-border-strong absolute inset-0 h-full w-full">
        <defs>
          <pattern
            id={patternId}
            width="9"
            height="9"
            patternUnits="userSpaceOnUse"
            patternTransform="rotate(45)"
          >
            <line x1="0" y1="0" x2="0" y2="9" stroke="currentColor" strokeWidth="1" />
          </pattern>
        </defs>
        <rect width="100%" height="100%" fill={`url(#${patternId})`} opacity="0.45" />
      </svg>
      <span className="bg-surface text-text-secondary border-border-subtle absolute top-3 left-3 rounded-full border px-2 py-0.5 text-xs lowercase">
        {pill}
      </span>
      <div
        className={`bg-surface shadow-pasture-sm relative flex h-12 w-12 items-center justify-center rounded-xl ${tint}`}
      >
        <Icon size={22} aria-hidden="true" />
      </div>
    </div>
  );
}

/* ------------------------------------------------------------------ */
/*  Artifact card                                                      */
/* ------------------------------------------------------------------ */

interface ArtifactCardProps {
  name: string;
  sizeBytes: number;
  slug: string;
  onDownload: (name: string) => void;
  onDelete: (name: string) => void;
  isDeleting: boolean;
}

function ArtifactCard({
  name,
  sizeBytes,
  slug,
  onDownload,
  onDelete,
  isDeleting,
}: ArtifactCardProps): JSX.Element {
  const type = deriveArtifactType(name);
  const title = deriveTitle(name);
  const prov = parseProvenance(name);
  const size = formatAttachmentSize(sizeBytes) ?? '—';
  const hasProvenance = Boolean(prov.threadId || prov.agent);

  return (
    <article className="bg-surface border-border-default shadow-pasture-sm hover:border-border-strong flex flex-col overflow-hidden rounded-lg border transition-colors">
      <ThumbnailHeader type={type} />

      <div className="flex flex-1 flex-col p-4">
        {/* Title — font-display heading; canonical name on hover. */}
        <h3
          className="font-display text-text-primary text-sm font-medium break-words"
          title={name}
        >
          {title}
        </h3>

        {/* Provenance — parsed from the name; omitted entirely when absent. */}
        {hasProvenance && (
          <p className="text-text-muted mt-1 flex flex-wrap items-center gap-x-1.5 text-xs">
            {prov.threadId && (
              <IdBadge
                id={prov.threadId}
                kind="thread"
                to={`/orgs/${slug}/threads/${prov.threadId}`}
              />
            )}
            {prov.threadId && prov.agent && <span aria-hidden="true">·</span>}
            {prov.agent && <span>{prov.agent}</span>}
            {prov.agent && prov.date && <span aria-hidden="true">·</span>}
            {prov.date && <span>{formatProvenanceDate(prov.date)}</span>}
          </p>
        )}

        {/* Footer: size + read actions (Download primary, Delete de-emphasised). */}
        <div className="mt-auto flex items-center justify-between gap-3 pt-3">
          <span className="text-text-muted font-mono text-xs tabular-nums">{size}</span>
          <div className="flex items-center gap-3">
            <button
              type="button"
              onClick={() => onDownload(name)}
              className="text-accent-text inline-flex items-center gap-1 text-xs hover:underline"
            >
              <Download size={14} aria-hidden="true" />
              Download
            </button>
            <button
              type="button"
              onClick={() => onDelete(name)}
              disabled={isDeleting}
              aria-label={`Delete ${name}`}
              className="text-text-muted hover:text-feedback-danger inline-flex items-center text-xs transition-colors disabled:opacity-50"
            >
              <Trash2 size={14} aria-hidden="true" />
            </button>
          </div>
        </div>
      </div>
    </article>
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
  const [activeFilter, setActiveFilter] = useState<ArtifactType | 'all'>('all');

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
  // Stable reference so the derived useMemos below don't recompute every render.
  const artifacts = useMemo(() => listQuery.data?.artifacts ?? [], [listQuery.data]);

  // Recency-sorted (modified_at desc) once; filter is applied on top per-render.
  const sorted = useMemo(
    () => [...artifacts].sort((a, b) => b.modified_at.localeCompare(a.modified_at)),
    [artifacts],
  );
  const visible = useMemo(
    () =>
      activeFilter === 'all'
        ? sorted
        : sorted.filter((a) => deriveArtifactType(a.name) === activeFilter),
    [sorted, activeFilter],
  );

  // Eyebrow counts: artifacts from the list; threads = distinct parsed THR ids.
  const threadCount = useMemo(() => {
    const ids = new Set<string>();
    for (const a of artifacts) {
      const { threadId } = parseProvenance(a.name);
      if (threadId) ids.add(threadId);
    }
    return ids.size;
  }, [artifacts]);

  const artifactCount = artifacts.length;
  const eyebrow =
    `${artifactCount} artifact${artifactCount === 1 ? '' : 's'}` +
    (threadCount > 0
      ? ` · produced by ${threadCount} thread${threadCount === 1 ? '' : 's'}`
      : '');

  const hasData = !listQuery.isLoading && !listQuery.isError;

  return (
    <div className="bg-surface-canvas flex h-full flex-col">
      {/* Header — eyebrow + serif title (ART-03); Upload de-emphasised (ART-04). */}
      <header className="border-border-default border-b p-6">
        <div className="flex items-start justify-between gap-3">
          <div className="min-w-0">
            {hasData && (
              <p className="text-text-secondary text-xs font-semibold tracking-wider uppercase">
                {eyebrow}
              </p>
            )}
            <h1 className="font-display text-display text-text-primary mt-2 font-medium">
              Everything the org has produced
            </h1>
          </div>
          <Button variant="ghost" size="sm" onClick={() => setShowUpload((v) => !v)}>
            <Upload aria-hidden="true" size={14} />
            {showUpload ? 'Cancel' : 'Upload'}
          </Button>
        </div>

        {/* Upload form (collapsible) — secondary affordance, not primary chrome. */}
        {showUpload && (
          <section
            aria-label="Upload artifact"
            className="bg-surface border-border-default shadow-pasture-sm mt-4 flex flex-col gap-3 rounded-lg border p-4"
          >
            <h3 className="text-text-primary text-sm font-semibold">Upload artifact</h3>
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
              <p className="text-text-muted text-xs">
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
      <div className="flex-1 overflow-y-auto p-6">
        {/* Loading */}
        {listQuery.isLoading && <ArtifactsSkeleton />}

        {/* Error */}
        {listQuery.isError && (
          <div className="flex flex-col items-center justify-center gap-3 p-8 text-center">
            <p className="text-feedback-danger text-sm">
              Could not load artifacts.
              {listQuery.error?.message && <> {listQuery.error.message}</>}
            </p>
            <Button
              size="sm"
              variant="outline"
              onClick={() => qc.invalidateQueries({ queryKey: ['artifacts', slug] })}
            >
              Retry
            </Button>
          </div>
        )}

        {/* Empty — calm empty state */}
        {hasData && artifacts.length === 0 && (
          <div className="flex h-full items-center justify-center">
            <EmptyState
              title="No artifacts yet"
              body="Upload a file above to share it across the org."
            />
          </div>
        )}

        {/* Card grid */}
        {hasData && artifacts.length > 0 && (
          <>
            {/* Filter (segmented) + sort row (ART-02) */}
            <div className="mb-5 flex flex-wrap items-center justify-between gap-3">
              <div
                role="tablist"
                aria-label="Filter artifacts by type"
                className="flex flex-wrap items-center gap-1"
              >
                {FILTERS.map((f) => {
                  const active = activeFilter === f.key;
                  return (
                    <button
                      key={f.key}
                      type="button"
                      role="tab"
                      aria-selected={active}
                      onClick={() => setActiveFilter(f.key)}
                      className={
                        active
                          ? 'bg-accent-muted text-accent-text rounded-full px-3 py-1 text-sm font-medium'
                          : 'text-text-secondary hover:text-text-primary hover:bg-surface-hover rounded-full px-3 py-1 text-sm'
                      }
                    >
                      {f.label}
                    </button>
                  );
                })}
              </div>
              <span className="text-text-muted text-sm">Recent first</span>
            </div>

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

            {visible.length === 0 ? (
              <p className="text-text-muted text-sm">No artifacts match this filter.</p>
            ) : (
              <div
                aria-label="Artifacts list"
                className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3"
              >
                {visible.map((a) => (
                  <ArtifactCard
                    key={a.name}
                    name={a.name}
                    sizeBytes={a.size_bytes}
                    slug={slug}
                    onDownload={(artifactName) => {
                      setDownloadError(null);
                      artifactsApi.downloadArtifact(slug, artifactName).catch((err: unknown) => {
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
            )}
          </>
        )}
      </div>
    </div>
  );
}
