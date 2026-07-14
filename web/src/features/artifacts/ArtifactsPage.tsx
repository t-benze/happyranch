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
 *
 * Folder tree (THR-061 slice 8): when artifact names carry a '/'-separated path
 * (CLAUDE.md documents '/' as the logical folder separator), the page becomes a
 * navigable tree — subfolder rows + a breadcrumb, all DERIVED client-side from
 * the name segments (see ./artifact-tree). No new route (the current folder is
 * in-memory state) and no new field. When every name is flat the tree chrome is
 * absent and the page stays the flat recency grid, so the feature is additive.
 */
import { useId, useMemo, useRef, useState } from 'react';
import {
  Download,
  File,
  FileDiff,
  FileText,
  Folder,
  GitPullRequest,
  Home,
  Image,
  Info,
  Trash2,
  Upload,
} from 'lucide-react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { artifacts as artifactsApi, ApiError } from '@/lib/api';
import { useOrgSlug } from '@/lib/orgSlug';
import { formatAttachmentSize } from '@/lib/threadAttachments';
import { Button } from '@/design-system/primitives/Button';
import { Input } from '@/design-system/primitives/Input';
import { Label } from '@/design-system/primitives/Label';
import { ContentWrap } from '@/design-system/layouts/ContentWrap/ContentWrap';
import { EmptyState } from '@/design-system/patterns/EmptyState';
import { IdBadge } from '@/design-system/patterns/IdBadge';
import {
  deriveArtifactType,
  deriveTitle,
  formatProvenanceDate,
  parseProvenance,
  type ArtifactType,
} from './artifact-meta';
import { buildFolderView, hasFolders, type Crumb, type FolderEntry } from './artifact-tree';
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
/*  Folder tree — breadcrumb + folder rows (client-derived, slice 8)   */
/* ------------------------------------------------------------------ */

/** Root → cwd trail. Each hop navigates; the last is the current folder. */
function Breadcrumb({
  crumbs,
  onNavigate,
}: {
  crumbs: Crumb[];
  onNavigate: (path: string) => void;
}): JSX.Element {
  return (
    <nav
      aria-label="Folder breadcrumb"
      className="mb-4 flex flex-wrap items-center gap-1 text-sm"
    >
      {crumbs.map((c, i) => {
        const isLast = i === crumbs.length - 1;
        const home = i === 0 ? <Home size={14} aria-hidden="true" /> : null;
        return (
          <span key={c.path || 'root'} className="flex items-center gap-1">
            {i > 0 && (
              <span className="text-text-muted" aria-hidden="true">
                /
              </span>
            )}
            {isLast ? (
              <span
                aria-current="page"
                className="text-text-primary inline-flex items-center gap-1 px-2 py-0.5 font-medium"
              >
                {home}
                {c.label}
              </span>
            ) : (
              <button
                type="button"
                onClick={() => onNavigate(c.path)}
                className="text-text-secondary hover:text-text-primary hover:bg-surface-hover inline-flex items-center gap-1 rounded-md px-2 py-0.5 font-medium transition-colors"
              >
                {home}
                {c.label}
              </button>
            )}
          </span>
        );
      })}
    </nav>
  );
}

/** A single subfolder row — accent-tinted folder glyph, name, and file count. */
function FolderRow({
  folder,
  onOpen,
}: {
  folder: FolderEntry;
  onOpen: (path: string) => void;
}): JSX.Element {
  return (
    <button
      type="button"
      onClick={() => onOpen(folder.path)}
      className="bg-surface border-border-default shadow-pasture-sm hover:border-border-strong hover:bg-surface-hover flex items-center gap-3 rounded-lg border p-4 text-left transition-colors"
    >
      <span className="bg-accent-muted text-accent-text flex h-9 w-9 flex-none items-center justify-center rounded-xl">
        <Folder size={19} aria-hidden="true" />
      </span>
      <span className="min-w-0">
        <span className="font-display text-text-primary block truncate text-sm font-semibold">
          {folder.name}/
        </span>
        <span className="text-text-muted block text-xs">
          {folder.count} file{folder.count === 1 ? '' : 's'}
        </span>
      </span>
    </button>
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
  // Current folder within the client-derived tree ('' = root). Never a route.
  const [cwd, setCwd] = useState('');

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

  // Type filter applied first; the folder view then scopes to the current dir.
  const filtered = useMemo(
    () =>
      activeFilter === 'all'
        ? artifacts
        : artifacts.filter((a) => deriveArtifactType(a.name) === activeFilter),
    [artifacts, activeFilter],
  );

  // Folders exist only when some name carries a '/' path — otherwise the page
  // stays the flat recency grid (the tree chrome is purely additive).
  const showFolders = useMemo(() => hasFolders(artifacts), [artifacts]);
  const view = useMemo(
    () => buildFolderView(filtered, showFolders ? cwd : ''),
    [filtered, cwd, showFolders],
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
      {/* Header — eyebrow + serif title (ART-03); Upload de-emphasised (ART-04).
          EM ruling (THR-099 artifacts): mirror the tasks dual-cap — cap the
          pinned-header inner AND the scroll-body inner at the shared 1180
          `max-w-content` (26px pad) via <ContentWrap> so the header columns sit
          directly above the body columns. The header STAYS pinned (shrink-0,
          outside the scroll region) and keeps its full-width border-b; its
          <ContentWrap> overflow-y-auto is inert on this content-height header. */}
      <header className="border-border-default shrink-0 border-b">
        <ContentWrap>
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
        </ContentWrap>
      </header>

      {/* Main content. The h-full-centered empty state is kept OUTSIDE
          <ContentWrap>: ContentWrap's inner .wrap is content-height, so an
          `h-full` child collapses instead of centering (MEM-080). It renders
          directly in this `min-h-0 flex-1` body, which has a definite flex
          height, so the empty state stays vertically centered. Every other
          state (loading / error / grid) is capped by <ContentWrap> (1180 cap,
          26px pad, owns the scroll surface) so the card columns align under the
          header columns. */}
      <div className="min-h-0 flex-1">
        {hasData && artifacts.length === 0 ? (
          /* Empty — calm empty state (kept full-height centered) */
          <div className="flex h-full items-center justify-center">
            <EmptyState
              title="No artifacts yet"
              body="Upload a file above to share it across the org."
            />
          </div>
        ) : (
          <ContentWrap>
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

            {/* Card grid */}
            {hasData && artifacts.length > 0 && (
          <>
            {/* Honesty note: folders are derived from the name path, not stored. */}
            {showFolders && (
              <div className="border-border-strong bg-surface-sunken text-text-muted mb-5 flex items-start gap-2 rounded-lg border border-dashed p-3 text-xs leading-relaxed">
                <Info
                  size={14}
                  aria-hidden="true"
                  className="text-attention-text mt-0.5 flex-none"
                />
                <p>
                  Folders are derived from the path in each artifact&apos;s{' '}
                  <span className="font-mono">name</span> — the only stored fields are{' '}
                  <span className="font-mono">name</span>,{' '}
                  <span className="font-mono">size_bytes</span>, and{' '}
                  <span className="font-mono">modified_at</span>. Agent and date come from the{' '}
                  <span className="font-mono">&lt;agent&gt;-&lt;YYYY-MM-DD&gt;-&lt;slug&gt;</span>{' '}
                  filename convention; there is no separate folder record.
                </p>
              </div>
            )}

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
              <span className="text-text-muted text-sm">
                {showFolders ? 'Folders first · recent files' : 'Recent first'}
              </span>
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

            {/* Breadcrumb — client-derived folder trail (only when folders exist). */}
            {showFolders && <Breadcrumb crumbs={view.crumbs} onNavigate={setCwd} />}

            {/* Subfolders of the current folder. */}
            {view.folders.length > 0 && (
              <div
                aria-label="Folders"
                className="mb-5 grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3"
              >
                {view.folders.map((f) => (
                  <FolderRow key={f.path} folder={f} onOpen={setCwd} />
                ))}
              </div>
            )}

            {/* Files directly in the current folder. */}
            {view.folders.length === 0 && view.files.length === 0 ? (
              <p className="text-text-muted text-sm">
                No artifacts match this filter{cwd ? ' in this folder' : ''}.
              </p>
            ) : (
              view.files.length > 0 && (
                <>
                  {view.folders.length > 0 && (
                    <p className="text-text-muted mb-3 text-xs font-semibold tracking-wide uppercase">
                      Files here
                    </p>
                  )}
                  <div
                    aria-label="Artifacts list"
                    className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3"
                  >
                    {view.files.map((a) => (
                      <ArtifactCard
                        key={a.name}
                        name={a.name}
                        sizeBytes={a.size_bytes}
                        slug={slug}
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
              )
            )}
          </>
            )}
          </ContentWrap>
        )}
      </div>
    </div>
  );
}
