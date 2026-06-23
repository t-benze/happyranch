/**
 * KbFolderRail — KB-01: the Knowledge folder rail, grouped into three
 * labeled sections (Library / Engineering / Org) with a folder icon and a
 * live per-folder count on every row.
 *
 * Honesty / data fence: sections and counts derive ONLY from the `type`
 * field the /kb list payload already returns (the same field the rail filters
 * by). Counts are computed client-side from the already-fetched unfiltered
 * entry set. The Engineering/Org split is a fixed presentation classification
 * of the live `type` vocabulary — NOT a backed field — so the reference's
 * illustrative sub-folders (review/qa/build · protocols/from-dreams) are
 * intentionally not reproduced. Only sections/folders that actually have
 * entries render; no empty/zero-count folders are fabricated.
 */
import type { ReactNode } from 'react';
import { cn } from '@/lib/utils';
import { KB_STRINGS } from './strings';

export interface KbFolder {
  /** The KB entry `type` this folder filters by. */
  value: string;
  /** Live count of entries of this type (unfiltered total). */
  count: number;
}

export interface KbFolderRailProps {
  /** Total entries across all folders — the "All entries" count. */
  total: number;
  /** Every folder present in the data, with its count. */
  folders: KbFolder[];
  /** Currently selected type, or null for "All entries". */
  selected: string | null;
  onSelect: (type: string | null) => void;
}

/** Org-governance folder types; everything else groups under Engineering. */
const ORG_TYPES = new Set(['ruling', 'sop']);

/** Decorative folder glyph — inherits the row's text color via currentColor. */
function FolderIcon(): JSX.Element {
  return (
    <svg
      className="shrink-0"
      width="14"
      height="14"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      <path d="M4 20a2 2 0 0 1-2-2V6a2 2 0 0 1 2-2h5l2 3h7a2 2 0 0 1 2 2v9a2 2 0 0 1-2 2Z" />
    </svg>
  );
}

function rowClasses(active: boolean): string {
  return cn(
    'flex w-full items-center gap-2 rounded-full px-2.5 py-1 text-left text-sm',
    active
      ? 'bg-accent-muted text-accent-text font-medium'
      : 'text-text-muted hover:bg-surface-raised',
  );
}

/** A single folder row: icon · label · count (icon + count are decorative). */
function FolderRow({
  folder,
  active,
  onSelect,
}: {
  folder: KbFolder;
  active: boolean;
  onSelect: (type: string) => void;
}): JSX.Element {
  return (
    <li>
      <button
        type="button"
        onClick={() => onSelect(folder.value)}
        className={rowClasses(active)}
      >
        <FolderIcon />
        <span className="truncate">{folder.value}</span>
        <span aria-hidden="true" className="ml-auto font-mono text-xs">
          {folder.count}
        </span>
      </button>
    </li>
  );
}

function Section({
  label,
  children,
}: {
  label: string;
  children: ReactNode;
}): JSX.Element {
  return (
    <section className="mb-4">
      <h3 className="text-text-muted font-display mb-2 text-xs font-medium tracking-wider uppercase">
        {label}
      </h3>
      <ul className="space-y-0.5">{children}</ul>
    </section>
  );
}

export function KbFolderRail({
  total,
  folders,
  selected,
  onSelect,
}: KbFolderRailProps): JSX.Element {
  const engineering = folders
    .filter((f) => !ORG_TYPES.has(f.value))
    .sort((a, b) => a.value.localeCompare(b.value));
  const org = folders
    .filter((f) => ORG_TYPES.has(f.value))
    .sort((a, b) => a.value.localeCompare(b.value));

  return (
    <div>
      <Section label={KB_STRINGS.railSectionLibrary}>
        <li>
          <button
            type="button"
            onClick={() => onSelect(null)}
            className={rowClasses(selected == null)}
          >
            <FolderIcon />
            <span className="truncate">{KB_STRINGS.railAllEntries}</span>
            <span aria-hidden="true" className="ml-auto font-mono text-xs">
              {total}
            </span>
          </button>
        </li>
      </Section>

      {engineering.length > 0 && (
        <Section label={KB_STRINGS.railSectionEngineering}>
          {engineering.map((f) => (
            <FolderRow
              key={f.value}
              folder={f}
              active={selected === f.value}
              onSelect={onSelect}
            />
          ))}
        </Section>
      )}

      {org.length > 0 && (
        <Section label={KB_STRINGS.railSectionOrg}>
          {org.map((f) => (
            <FolderRow
              key={f.value}
              folder={f}
              active={selected === f.value}
              onSelect={onSelect}
            />
          ))}
        </Section>
      )}
    </div>
  );
}
