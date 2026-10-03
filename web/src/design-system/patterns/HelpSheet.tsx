/**
 * HelpSheet — keyboard-shortcut reference rendered as a Dialog.
 *
 * Two modes:
 *
 * 1. **Flat (legacy)** — pass `shortcuts: ShortcutEntry[]`. Renders the entries
 *    as a single column. This is what `ThreadsPage` used before PR 13. Still
 *    supported so the design-system route and any one-off uses keep working.
 *
 * 2. **Tabbed (PR 13)** — pass `sections: ShortcutSection[]`. Renders a tab
 *    bar (one tab per feature) and shows the active tab's shortcuts. Used by
 *    the global `HelpDrawerHost` mounted in AppShell — the founder presses
 *    `?` on any surface and sees the full reference, switching tabs without
 *    leaving the page.
 *
 * Pure prop-driven — the shortcut lists live in each feature folder
 * (`*-shortcuts.ts`) so this pattern stays a presentation primitive.
 */
import * as React from 'react';

import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from '@/design-system/primitives/Dialog';
import {
  Tabs,
  TabsContent,
  TabsList,
  TabsTrigger,
} from '@/design-system/primitives/Tabs';
import { KbdChip } from './KbdChip';

export interface ShortcutEntry {
  keys: string[];
  description: string;
}

export interface ShortcutSection {
  /**
   * Stable section identity (`global`, `threads`, …). Tab VALUE/keys use this
   * when present, so a localized-`label` change (language switch) never resets
   * the selected tab. Defaults to `label` for backward compatibility.
   */
  id?: string;
  /** Tab label — "Global", "Threads", "Tasks", … (localized by the caller). */
  label: string;
  shortcuts: ShortcutEntry[];
}

interface HelpSheetPropsBase {
  open: boolean;
  onClose: () => void;
  /** Subtitle shown below the list. Defaults to the focus-restriction note. */
  footnote?: string;
  /** Optional localized strings; the pattern stays pure and prop-driven. */
  title?: string;
  description?: string;
  emptyLabel?: string;
  /** Optional localized accessible label for the dialog's close control. */
  closeLabel?: string;
}

interface HelpSheetPropsFlat extends HelpSheetPropsBase {
  shortcuts: ShortcutEntry[];
  sections?: never;
  defaultTab?: never;
  defaultTabId?: never;
}

interface HelpSheetPropsTabbed extends HelpSheetPropsBase {
  sections: ShortcutSection[];
  /** Tab to show on open, matched by section `label`. Defaults to the first section. */
  defaultTab?: string;
  /** Tab to show on open, matched by stable section `id`. Takes precedence. */
  defaultTabId?: string;
  shortcuts?: never;
}

type HelpSheetProps = HelpSheetPropsFlat | HelpSheetPropsTabbed;

const DEFAULT_FOOTNOTE =
  'Shortcuts are disabled while focus is inside an input or textarea.';

function ShortcutList({
  shortcuts,
}: {
  shortcuts: ShortcutEntry[];
}): JSX.Element {
  return (
    <ul className="flex flex-col gap-1.5">
      {shortcuts.map((s) => (
        <li
          key={s.keys.join('+') + ':' + s.description}
          className="text-body flex items-center gap-3"
        >
          <span className="min-w-[5rem]">
            <KbdChip keys={s.keys} />
          </span>
          <span className="text-text-muted">{s.description}</span>
        </li>
      ))}
    </ul>
  );
}

export function HelpSheet(props: HelpSheetProps): JSX.Element {
  const isTabbed = 'sections' in props && props.sections !== undefined;
  const footnote = props.footnote ?? DEFAULT_FOOTNOTE;

  return (
    <Dialog open={props.open} onOpenChange={(o) => { if (!o) props.onClose(); }}>
      <DialogContent closeLabel={props.closeLabel}>
        <DialogHeader>
          <DialogTitle>{props.title ?? 'Keyboard shortcuts'}</DialogTitle>
          <DialogDescription className="sr-only">
            {props.description ?? 'List of keyboard shortcuts available on this screen.'}
          </DialogDescription>
        </DialogHeader>
        {isTabbed ? (
          <TabbedBody
            sections={(props as HelpSheetPropsTabbed).sections}
            defaultTab={(props as HelpSheetPropsTabbed).defaultTab}
            defaultTabId={(props as HelpSheetPropsTabbed).defaultTabId}
            emptyLabel={props.emptyLabel}
          />
        ) : (
          <ShortcutList
            shortcuts={(props as HelpSheetPropsFlat).shortcuts}
          />
        )}
        {footnote && (
          <p className="text-caption text-text-muted mt-3">{footnote}</p>
        )}
      </DialogContent>
    </Dialog>
  );
}

/** Stable tab value: the section `id` when supplied, otherwise the label. */
function sectionKey(section: ShortcutSection): string {
  return section.id ?? section.label;
}

function TabbedBody({
  sections,
  defaultTab,
  defaultTabId,
  emptyLabel,
}: {
  sections: ShortcutSection[];
  defaultTab?: string;
  defaultTabId?: string;
  emptyLabel?: string;
}): JSX.Element {
  const tabs = sections.filter((s) => s.shortcuts.length > 0);
  const matched =
    (defaultTabId && tabs.find((t) => sectionKey(t) === defaultTabId)) ||
    (defaultTab && tabs.find((t) => t.label === defaultTab));
  const initial = matched ? sectionKey(matched) : tabs[0] ? sectionKey(tabs[0]) : '';
  const [active, setActive] = React.useState<string>(initial);
  // Sync the active tab when the host swaps sections (e.g., route change
  // alters which feature owns "active"). Matching by stable key means a
  // localized label change keeps the selected tab.
  React.useEffect(() => {
    if (!tabs.some((t) => sectionKey(t) === active)) {
      setActive(tabs[0] ? sectionKey(tabs[0]) : '');
    }
  }, [tabs, active]);

  if (tabs.length === 0) {
    return (
      <p className="text-caption text-text-muted">
        {emptyLabel ?? 'No shortcuts defined.'}
      </p>
    );
  }

  return (
    <Tabs value={active} onValueChange={setActive}>
      <TabsList className="flex-wrap">
        {tabs.map((s) => (
          <TabsTrigger key={sectionKey(s)} value={sectionKey(s)}>
            {s.label}
          </TabsTrigger>
        ))}
      </TabsList>
      {tabs.map((s) => (
        <TabsContent key={sectionKey(s)} value={sectionKey(s)}>
          <ShortcutList shortcuts={s.shortcuts} />
        </TabsContent>
      ))}
    </Tabs>
  );
}
