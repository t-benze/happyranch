/**
 * Client-side derivations for the Artifacts card grid (THR-030 ART-01/02/04).
 *
 * The daemon artifact list returns ONLY stored fields: `name`, `size_bytes`,
 * `modified_at` (see runtime/infrastructure/artifact_store.py ArtifactInfo).
 * There is NO stored type, status, agent, thread, or authored-at. So a card's
 * type pill + icon and its provenance line are DERIVED here, purely from the
 * file name, following the org's documented naming convention
 * (`<agent>-<YYYY-MM-DD>-<slug>`, see CLAUDE.md "Shared Artifacts").
 *
 * Honesty rule: derive only what the name actually encodes. For a name that
 * does not match the convention, return a neutral/null shape — never fabricate
 * an agent, date, or thread id. The authoritative status pill
 * (merged/draft/open/…) and server-captured provenance are intentionally NOT
 * derived here: they have no data source and are deferred to a backend change.
 */

/** The five card categories the segmented filter exposes (plus the catch-all). */
export type ArtifactType = 'pull-request' | 'doc' | 'patch' | 'design' | 'file';

export interface ArtifactProvenance {
  /** Authoring agent, parsed from the name prefix; null when not present. */
  agent: string | null;
  /** Authoring date as embedded in the name (`YYYY-MM-DD`); null when absent. */
  date: string | null;
  /** Embedded thread id (`THR-NNN`, upper-cased) if the name carries one. */
  threadId: string | null;
}

/** Extension → category. Compound names (`a.tar.gz`) match on the final segment. */
const DOC_EXTS = new Set(['md', 'markdown', 'txt', 'pdf', 'rst', 'doc', 'docx']);
const DESIGN_EXTS = new Set(['png', 'jpg', 'jpeg', 'gif', 'svg', 'webp', 'fig', 'sketch', 'ai', 'psd']);
const PATCH_EXTS = new Set(['patch', 'diff']);

/**
 * A pull-request reference: literally "pull request" or a `pr` token glued to a
 * number (`PR-101`, `pr_104`, `PR#88`). The trailing digits are load-bearing —
 * they keep ordinary words like "product"/"approve"/"sprint" from matching.
 */
const PR_RE = /\bpr[-_ ]?#?\d+\b|pull[-_ ]?request/i;

const THR_RE = /\bthr-(\d+)\b/i;

/** `<agent>-<YYYY-MM-DD>-<slug>` — agent is a snake/lower token, slug is the rest. */
const PROVENANCE_RE = /^([a-z][a-z0-9_]*)-(\d{4}-\d{2}-\d{2})-(.+)$/i;

function extensionOf(name: string): string {
  const dot = name.lastIndexOf('.');
  return dot === -1 ? '' : name.slice(dot + 1).toLowerCase();
}

export function deriveArtifactType(name: string): ArtifactType {
  // An explicit PR token wins over the extension (a PR write-up may be a .md).
  if (PR_RE.test(name)) return 'pull-request';
  const ext = extensionOf(name);
  if (DOC_EXTS.has(ext)) return 'doc';
  if (PATCH_EXTS.has(ext)) return 'patch';
  if (DESIGN_EXTS.has(ext)) return 'design';
  return 'file';
}

export function parseProvenance(name: string): ArtifactProvenance {
  const thr = name.match(THR_RE);
  const threadId = thr ? `THR-${thr[1]}` : null;
  const m = name.match(PROVENANCE_RE);
  if (!m) return { agent: null, date: null, threadId };
  return { agent: m[1], date: m[2], threadId };
}

/** Clean display title: the slug after the `<agent>-<date>-` prefix, else the name. */
export function deriveTitle(name: string): string {
  const m = name.match(PROVENANCE_RE);
  return m ? m[3] : name;
}

const MONTHS = [
  'Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun',
  'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec',
];

/** Format a plain `YYYY-MM-DD` (no time component, so no timezone drift). */
export function formatProvenanceDate(date: string): string {
  const [y, m, d] = date.split('-').map((n) => Number.parseInt(n, 10));
  return `${MONTHS[m - 1]} ${d}, ${y}`;
}
