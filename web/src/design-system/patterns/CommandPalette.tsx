/**
 * CommandPalette — keyboard-driven entity jumper.
 *
 * Pure presentation. The host (`features/command-palette/CommandPaletteHost`)
 * gathers data from React Query caches, owns the open state, and feeds
 * sections in here. The pattern does NOT import any feature hook so it
 * can be exercised under the design-system route with canned fixtures.
 *
 * Per spec `2026-05-19-web-polish-design.md` §6.
 */
import { Search } from 'lucide-react';
import * as React from 'react';

import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogTitle,
} from '@/design-system/primitives/Dialog';
import { cn } from '@/lib/utils';

/** A row in the palette — one indexable entity. */
export interface CommandPaletteItem {
  /** Stable key (e.g. `task:TASK-4421`, `kb:hk-visa-rules`). */
  key: string;
  /** Headline string. Indexed for matching. */
  primary: string;
  /** Optional muted suffix shown to the right of `primary`. Indexed too. */
  secondary?: string;
  /** Per-item navigation target. */
  href: string;
}

/** A grouped section. Empty sections (`items: []`) are hidden by the pattern. */
export interface CommandPaletteSection {
  /** Section heading (overline-case rendered). */
  label: string;
  items: CommandPaletteItem[];
}

interface CommandPaletteProps {
  open: boolean;
  onClose: () => void;
  sections: CommandPaletteSection[];
  /** Called with the chosen item's `href` on Enter / click. */
  onSelect: (href: string, item: CommandPaletteItem) => void;
  /** Max items per section in the rendered list. Default 5. */
  perSectionLimit?: number;
}

function matches(item: CommandPaletteItem, q: string): boolean {
  if (!q) return true;
  const haystack = `${item.primary} ${item.secondary ?? ''}`.toLowerCase();
  return haystack.includes(q.toLowerCase());
}

interface FlatRow {
  kind: 'header' | 'item';
  sectionLabel: string;
  item?: CommandPaletteItem;
  itemIndex?: number;
}

export function CommandPalette({
  open,
  onClose,
  sections,
  onSelect,
  perSectionLimit = 5,
}: CommandPaletteProps): JSX.Element {
  const [query, setQuery] = React.useState('');
  const [activeIndex, setActiveIndex] = React.useState(0);
  const inputRef = React.useRef<HTMLInputElement | null>(null);

  React.useEffect(() => {
    if (!open) {
      setQuery('');
      setActiveIndex(0);
    } else {
      // Focus the search input after the dialog mounts. Replaces the
      // `autoFocus` attribute (banned by jsx-a11y/no-autofocus).
      requestAnimationFrame(() => inputRef.current?.focus());
    }
  }, [open]);

  const filtered = React.useMemo(() => {
    return sections
      .map((section) => ({
        label: section.label,
        items: section.items.filter((i) => matches(i, query)).slice(0, perSectionLimit),
      }))
      .filter((section) => section.items.length > 0);
  }, [sections, query, perSectionLimit]);

  // Flatten into a linear list so ↑↓ navigation feels right across sections.
  const rows: FlatRow[] = React.useMemo(() => {
    const out: FlatRow[] = [];
    for (const section of filtered) {
      out.push({ kind: 'header', sectionLabel: section.label });
      section.items.forEach((item, itemIndex) => {
        out.push({
          kind: 'item',
          sectionLabel: section.label,
          item,
          itemIndex,
        });
      });
    }
    return out;
  }, [filtered]);

  const itemRows = React.useMemo(
    () => rows.filter((r) => r.kind === 'item'),
    [rows],
  );

  React.useEffect(() => {
    setActiveIndex((current) => {
      if (itemRows.length === 0) return 0;
      if (current >= itemRows.length) return 0;
      return current;
    });
  }, [itemRows.length]);

  const handleKeyDown = (ev: React.KeyboardEvent<HTMLDivElement>) => {
    if (ev.key === 'ArrowDown') {
      ev.preventDefault();
      if (itemRows.length > 0) {
        setActiveIndex((i) => (i + 1) % itemRows.length);
      }
    } else if (ev.key === 'ArrowUp') {
      ev.preventDefault();
      if (itemRows.length > 0) {
        setActiveIndex((i) => (i - 1 + itemRows.length) % itemRows.length);
      }
    } else if (ev.key === 'Enter') {
      ev.preventDefault();
      const active = itemRows[activeIndex];
      if (active?.item) {
        onSelect(active.item.href, active.item);
      }
    }
  };

  const activeKey = itemRows[activeIndex]?.item?.key;

  return (
    <Dialog open={open} onOpenChange={(o) => { if (!o) onClose(); }}>
      <DialogContent
        className="top-[20%] max-w-[480px] translate-y-0 gap-0 p-0"
        onKeyDown={handleKeyDown}
        aria-describedby="command-palette-help"
      >
        <DialogTitle className="sr-only">Command palette</DialogTitle>
        <DialogDescription id="command-palette-help" className="sr-only">
          Type to filter. Use up and down arrows to move. Press Enter to open.
          Press Escape to close.
        </DialogDescription>
        <div className="border-border flex items-center gap-2 border-b px-3 py-2">
          <Search size={16} aria-hidden="true" className="text-fg-muted" />
          <input
            ref={inputRef}
            type="text"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="Search threads, tasks, agents, orgs, KB…"
            aria-label="Command palette search"
            aria-controls="command-palette-listbox"
            role="combobox"
            aria-expanded={true}
            aria-activedescendant={activeKey ? `cmdk-row-${activeKey}` : undefined}
            className="text-fg placeholder:text-fg-subtle w-full bg-transparent text-sm focus:outline-none"
          />
        </div>
        <div
          id="command-palette-listbox"
          role="listbox"
          aria-label="Results"
          className="max-h-[50vh] overflow-y-auto py-2"
        >
          {itemRows.length === 0 && (
            <div className="text-fg-muted px-4 py-6 text-center text-sm">
              {query ? 'No matches.' : 'Nothing loaded yet — visit a page first.'}
            </div>
          )}
          {rows.map((row) => {
            if (row.kind === 'header') {
              return (
                <div
                  key={`hdr-${row.sectionLabel}`}
                  className="text-fg-subtle px-3 pt-3 pb-1 text-[0.6875rem] font-semibold tracking-wider uppercase"
                >
                  {row.sectionLabel}
                </div>
              );
            }
            const item = row.item;
            if (!item) return null;
            const linearIndex = itemRows.indexOf(row);
            const isActive = linearIndex === activeIndex;
            return (
              <button
                key={item.key}
                id={`cmdk-row-${item.key}`}
                type="button"
                role="option"
                aria-selected={isActive}
                onMouseEnter={() => setActiveIndex(linearIndex)}
                onClick={() => onSelect(item.href, item)}
                className={cn(
                  'flex w-full items-center gap-2 px-3 py-1.5 text-left text-sm transition-colors',
                  isActive ? 'bg-accent/15 text-fg' : 'text-fg-muted hover:bg-bg-raised hover:text-fg',
                )}
              >
                <span className="truncate">{item.primary}</span>
                {item.secondary && (
                  <span className="text-fg-subtle truncate text-xs">
                    {item.secondary}
                  </span>
                )}
              </button>
            );
          })}
        </div>
        <div className="border-border text-fg-subtle border-t px-3 py-1.5 text-[11px]">
          <span className="font-mono">↑↓</span> navigate ·{' '}
          <span className="font-mono">⏎</span> open ·{' '}
          <span className="font-mono">esc</span> close
        </div>
      </DialogContent>
    </Dialog>
  );
}

export const meta = {
  name: 'CommandPalette',
  layer: 'pattern',
  import: '@/design-system/patterns/CommandPalette',
  variants: {},
  consumes: ['components.dialog'],
  example:
    "<CommandPalette open={false} onClose={() => {}} sections={[]} onSelect={() => {}} />",
} as const;
