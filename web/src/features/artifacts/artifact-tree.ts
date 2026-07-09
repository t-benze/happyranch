/**
 * Client-side folder tree for the Artifacts card grid (THR-061 slice 8).
 *
 * Artifact names may embed a logical folder path — CLAUDE.md "Shared Artifacts"
 * documents '/' as the folder separator (e.g. `reports/qa/qa_engineer-…-x.md`).
 * The daemon stores NO folder record: the list route returns only `name`,
 * `size_bytes`, `modified_at` (see runtime/infrastructure/artifact_store.py).
 * So the tree here is DERIVED purely from the '/' segments of the names —
 * additive, honest, and requires no new route or field. `cwd` is in-memory UI
 * state (the current folder), never a URL route.
 */
/**
 * The subset of the artifact-list payload this pure module needs. Declared
 * structurally (not imported from `@/lib/api`) so the tree logic stays a plain
 * derivation with no data-layer coupling — the composition owns the fetch.
 */
export interface ArtifactItem {
  name: string;
  size_bytes: number;
  modified_at: string;
}

/** An immediate subfolder of the current folder, with a direct+nested file count. */
export interface FolderEntry {
  /** The folder's own segment name (no path), e.g. `qa`. */
  name: string;
  /** The full path to navigate into, e.g. `reports/qa`. */
  path: string;
  /** How many artifacts live at or below this folder (within the current filter). */
  count: number;
}

/** One breadcrumb hop. `path === ''` is the root ("Artifacts"). */
export interface Crumb {
  label: string;
  path: string;
}

export interface FolderView {
  /** Immediate subfolders of `cwd`, sorted by name. */
  folders: FolderEntry[];
  /** Files directly in `cwd` (no deeper folder), most-recent-first. */
  files: ArtifactItem[];
  /** Root → … → cwd breadcrumb trail (always starts at the "Artifacts" root). */
  crumbs: Crumb[];
}

/** True when any artifact name carries a folder path — i.e. the tree is meaningful. */
export function hasFolders(items: ArtifactItem[]): boolean {
  return items.some((it) => it.name.includes('/'));
}

/**
 * Partition `items` (already type-filtered) into the immediate subfolders and
 * direct files of `cwd`, plus the breadcrumb trail. Pure and side-effect-free.
 */
export function buildFolderView(items: ArtifactItem[], cwd: string): FolderView {
  const prefix = cwd ? `${cwd}/` : '';
  const scoped = items.filter(
    (it) => it.name.startsWith(prefix) && it.name.length > prefix.length,
  );

  const folderCounts = new Map<string, number>();
  const files: ArtifactItem[] = [];
  for (const it of scoped) {
    const rest = it.name.slice(prefix.length);
    const slash = rest.indexOf('/');
    if (slash === -1) {
      files.push(it);
    } else {
      const seg = rest.slice(0, slash);
      folderCounts.set(seg, (folderCounts.get(seg) ?? 0) + 1);
    }
  }

  const folders: FolderEntry[] = [...folderCounts.entries()]
    .sort((a, b) => a[0].localeCompare(b[0]))
    .map(([name, count]) => ({ name, path: prefix + name, count }));

  // ISO-8601 "Z" strings sort lexicographically = chronologically (recent first).
  files.sort((a, b) => b.modified_at.localeCompare(a.modified_at));

  const crumbs: Crumb[] = [{ label: 'Artifacts', path: '' }];
  if (cwd) {
    let acc = '';
    for (const seg of cwd.split('/')) {
      acc = acc ? `${acc}/${seg}` : seg;
      crumbs.push({ label: seg, path: acc });
    }
  }

  return { folders, files, crumbs };
}
