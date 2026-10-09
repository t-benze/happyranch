import { IdentityName } from '@/shared/identities/IdentityName';
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
import { artifacts as artifactsApi } from '@/lib/api';
import { useOrgSlug } from '@/lib/orgSlug';
import { useTranslation } from '@/hooks/i18n';
import { formatCountFor, formatAttachmentSizeFor, type MessageKey } from '@/lib/i18n';
import { Button } from '@/design-system/primitives/Button';
import { Input } from '@/design-system/primitives/Input';
import { Label } from '@/design-system/primitives/Label';
import { ContentWrap } from '@/design-system/layouts/ContentWrap/ContentWrap';
import { EmptyState } from '@/design-system/patterns/EmptyState';
import { IdBadge } from '@/design-system/patterns/IdBadge';
import {
  deriveArtifactType,
  deriveTitle,
  formatArtifactModifiedAt,
  formatProvenanceDate,
  parseProvenance,
  type ArtifactType,
} from './artifact-meta';
import { buildFolderView, hasFolders, type Crumb, type FolderEntry } from './artifact-tree';
import { validateArtifactUpload, classifyArtifactError, renderArtifactError, type ArtifactErrorView } from './validation';

/* ------------------------------------------------------------------ */
/*  Type → presentation (pill label, centered icon, icon tint)         */
/* ------------------------------------------------------------------ */

const TYPE_META: Record<
  ArtifactType,
  { pill: MessageKey; Icon: typeof File; tint: string }
> = {
  'pull-request': { pill: 'artifacts.type.pull-request', Icon: GitPullRequest, tint: 'text-accent-text' },
  doc: { pill: 'artifacts.type.doc', Icon: FileText, tint: 'text-text-secondary' },
  patch: { pill: 'artifacts.type.patch', Icon: FileDiff, tint: 'text-accent-text' },
  design: { pill: 'artifacts.type.design', Icon: Image, tint: 'text-text-secondary' },
  file: { pill: 'artifacts.type.file', Icon: File, tint: 'text-text-muted' },
};

/** Segmented filter — "All" plus the four named categories ("file" lives in All). */
const FILTERS: { key: ArtifactType | 'all'; label: MessageKey }[] = [
  { key: 'all', label: 'artifacts.filter.all' },
  { key: 'pull-request', label: 'artifacts.filter.pull-request' },
  { key: 'doc', label: 'artifacts.filter.doc' },
  { key: 'patch', label: 'artifacts.filter.patch' },
  { key: 'design', label: 'artifacts.filter.design' },
];

/* ------------------------------------------------------------------ */
/*  Error helpers                                                      */
/* ------------------------------------------------------------------ */

/* ------------------------------------------------------------------ */
/*  Skeleton                                                           */
/* ------------------------------------------------------------------ */

function ArtifactsSkeleton(): JSX.Element {
  const { t } = useTranslation();
  return (
    <div
      className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3"
      aria-label={t('artifacts.loading')}
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
  const { t } = useTranslation();
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
        {t(pill)}
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
  modifiedAt: string;
  slug: string;
  onDownload: (name: string) => void;
  onDelete: (name: string) => void;
  isDeleting: boolean;
}

function ArtifactCard({
  name,
  sizeBytes,
  modifiedAt,
  slug,
  onDownload,
  onDelete,
  isDeleting,
}: ArtifactCardProps): JSX.Element {
  const { t, locale } = useTranslation();
  const type = deriveArtifactType(name);
  const title = deriveTitle(name);
  const prov = parseProvenance(name);
  const size = formatAttachmentSizeFor(locale, sizeBytes) ?? '—';
  const modifiedDisplay = formatArtifactModifiedAt(modifiedAt, locale);
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

        {/*
          Provenance is filename-derived only. The authoritative agent/thread/time
          is not stored by the artifact route, so we label the parsed tokens as
          "From filename" and omit the whole block for neutral names.
        */}
        {hasProvenance && (
          <p className="text-text-muted mt-1 flex flex-wrap items-center gap-x-1.5 text-xs">
            <span className="sr-only">{t('artifacts.provenanceLabel')}</span>
            <span aria-hidden="true">{t('artifacts.fromFilename')}</span>
            {prov.threadId && (
              <>
                <span aria-hidden="true">·</span>
                <IdBadge
                  id={prov.threadId}
                  kind="thread"
                  to={`/orgs/${slug}/threads/${prov.threadId}`}
                />
              </>
            )}
            {prov.agent && (
              <>
                <span aria-hidden="true">·</span>
                <span><IdentityName canonicalId={prov.agent} /></span>
              </>
            )}
            {prov.date && (
              <>
                <span aria-hidden="true">·</span>
                <span>{formatProvenanceDate(prov.date, locale) ?? t('artifacts.dateUnavailable')}</span>
              </>
            )}
          </p>
        )}

        {/* File modification time supplied by the API; never invented. */}
        <p className="text-text-muted mt-1 text-xs">
          {modifiedDisplay ? (
            <>
              {t('artifacts.modified', { date: modifiedDisplay })}
            </>
          ) : (
            <span>{t('artifacts.modifiedUnavailable')}</span>
          )}
        </p>

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
              {t('artifacts.download')}
            </button>
            <button
              type="button"
              onClick={() => onDelete(name)}
              disabled={isDeleting}
              aria-label={t('artifacts.deleteLabel', { name })}
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
  const { t } = useTranslation();
  return (
    <nav
      aria-label={t('artifacts.breadcrumb')}
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
                {c.path === '' ? t('artifacts.root') : c.label}
              </span>
            ) : (
              <button
                type="button"
                onClick={() => onNavigate(c.path)}
                className="text-text-secondary hover:text-text-primary hover:bg-surface-hover inline-flex items-center gap-1 rounded-md px-2 py-0.5 font-medium transition-colors"
              >
                {home}
                {c.path === '' ? t('artifacts.root') : c.label}
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
  const { t, locale } = useTranslation();
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
          {t('artifacts.fileCount', { count: folder.count, number: formatCountFor(locale, folder.count) })}
        </span>
      </span>
    </button>
  );
}

/* ------------------------------------------------------------------ */
/*  Main component                                                     */
/* ------------------------------------------------------------------ */

export function ArtifactsPage(): JSX.Element {
  const { t, locale, render } = useTranslation();
  const slug = useOrgSlug();
  const qc = useQueryClient();
  const idBase = useId();
  const fileInputRef = useRef<HTMLInputElement>(null);
  const [file, setFile] = useState<File | null>(null);
  const [name, setName] = useState('');
  const [error, setError] = useState<ArtifactErrorView | null>(null);
  const [deleteError, setDeleteError] = useState<ArtifactErrorView | null>(null);
  const [downloadError, setDownloadError] = useState<ArtifactErrorView | null>(null);
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
    onError: (err: unknown) => setError(classifyArtifactError(err, 'artifacts.error.upload')),
  });

  const del = useMutation({
    mutationFn: (artifactName: string) => artifactsApi.deleteArtifact(slug, artifactName),
    onSuccess: () => {
      setDeleteError(null);
      qc.invalidateQueries({ queryKey: ['artifacts', slug] });
    },
    onError: (err: unknown) => setDeleteError(classifyArtifactError(err, 'artifacts.error.delete')),
  });

  const requestDelete = (artifactName: string) => {
    setDeleteError(null);
    if (!window.confirm(t('artifacts.deleteConfirm', { name: artifactName }))) return;
    del.mutate(artifactName);
  };

  const submit = () => {
    setError(null);
    if (!file) {
      setError({ kind: 'message', key: 'artifacts.error.selectFile' });
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
  const eyebrow = t('artifacts.artifactCount', { count: artifactCount, number: formatCountFor(locale, artifactCount) }) +
    (threadCount > 0 ? ` · ${t('artifacts.threadCount', { count: threadCount, number: formatCountFor(locale, threadCount) })}` : '');

  const hasData = !listQuery.isLoading && !listQuery.isError;
  const listError = listQuery.isError ? classifyArtifactError(listQuery.error, 'artifacts.loadError') : null;

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
              {t('artifacts.pageTitle')}
            </h1>
          </div>
          {/* Solid green "↑ Upload" pill per the a-artifacts reference (THR-099
              Batch 3); switches to a quiet ghost "Cancel" once the form is open
              so the dismiss action does not read as a second primary action. */}
          <Button
            variant={showUpload ? 'ghost' : 'default'}
            size="sm"
            onClick={() => setShowUpload((v) => !v)}
          >
            <Upload aria-hidden="true" size={14} />
            {showUpload ? t('common.cancel') : t('artifacts.upload')}
          </Button>
        </div>

        {/* Upload form (collapsible) — secondary affordance, not primary chrome. */}
        {showUpload && (
          <section
            aria-label={t('artifacts.uploadTitle')}
            className="bg-surface border-border-default shadow-pasture-sm mt-4 flex flex-col gap-3 rounded-lg border p-4"
          >
            <h3 className="text-text-primary text-sm font-semibold">{t('artifacts.uploadTitle')}</h3>
            <div className="flex flex-col gap-1">
              <Label htmlFor={fileId}>{t('artifacts.file')}</Label>
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
              <Label htmlFor={nameId}>{t('artifacts.nameLabel')}</Label>
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
                {t('artifacts.nameHint')}
              </p>
            </div>
            {error && (
              <p role="alert" className="text-feedback-danger text-sm">
                {renderArtifactError(error, t)}
              </p>
            )}
            <div>
              <Button onClick={submit} disabled={upload.isPending}>
                <Upload aria-hidden="true" size={14} />
                {upload.isPending ? t('artifacts.uploading') : t('artifacts.upload')}
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
              title={t('artifacts.emptyTitle')}
              body={t('artifacts.emptyBody')}
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
                  {t('artifacts.loadError')}
                  {listError && (listError.kind === 'raw' || listError.key !== 'artifacts.loadError') && <> {renderArtifactError(listError, t)}</>}
                </p>
                <Button
                  size="sm"
                  variant="outline"
                  onClick={() => qc.invalidateQueries({ queryKey: ['artifacts', slug] })}
                >
                  {t('common.retry')}
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
                  {render('artifacts.folderHonesty', {
                    nameField: <span className="font-mono">name</span>,
                    sizeField: <span className="font-mono">size_bytes</span>,
                    modifiedField: <span className="font-mono">modified_at</span>,
                    convention: <span className="font-mono">&lt;agent&gt;-&lt;YYYY-MM-DD&gt;-&lt;slug&gt;</span>,
                  })}
                </p>
              </div>
            )}

            {/* Filter (segmented) + sort row (ART-02) */}
            <div className="mb-5 flex flex-wrap items-center justify-between gap-3">
              <div
                role="tablist"
                aria-label={t('artifacts.filterLabel')}
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
                      {t(f.label)}
                    </button>
                  );
                })}
              </div>
              <span className="text-text-muted text-sm">
                {t(showFolders ? 'artifacts.sortFolders' : 'artifacts.sortRecent')}
              </span>
            </div>

            {/* Banner for delete/download errors */}
            {deleteError && (
              <p role="alert" className="text-feedback-danger mb-4 text-sm">
                {renderArtifactError(deleteError, t)}
              </p>
            )}
            {downloadError && !deleteError && (
              <p role="alert" className="text-feedback-danger mb-4 text-sm">
                {renderArtifactError(downloadError, t)}
              </p>
            )}

            {/* Breadcrumb — client-derived folder trail (only when folders exist). */}
            {showFolders && <Breadcrumb crumbs={view.crumbs} onNavigate={setCwd} />}

            {/* Subfolders of the current folder. */}
            {view.folders.length > 0 && (
              <div
                aria-label={t('artifacts.folders')}
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
                {t(cwd ? 'artifacts.emptyFolderFilter' : 'artifacts.emptyFilter')}
              </p>
            ) : (
              view.files.length > 0 && (
                <>
                  {view.folders.length > 0 && (
                    <p className="text-text-muted mb-3 text-xs font-semibold tracking-wide uppercase">
                      {t('artifacts.filesHere')}
                    </p>
                  )}
                  <div
                    aria-label={t('artifacts.listLabel')}
                    className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3"
                  >
                    {view.files.map((a) => (
                      <ArtifactCard
                        key={a.name}
                        name={a.name}
                        sizeBytes={a.size_bytes}
                        modifiedAt={a.modified_at}
                        slug={slug}
                        onDownload={(artifactName) => {
                          setDownloadError(null);
                          artifactsApi
                            .downloadArtifact(slug, artifactName)
                            .catch((err: unknown) => {
                              setDownloadError(classifyArtifactError(err, 'artifacts.error.download'));
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
