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
  /** Tab label — "Global", "Threads", "Tasks", … */
  label: string;
  shortcuts: ShortcutEntry[];
}

interface HelpSheetPropsBase {
  open: boolean;
  onClose: () => void;
  /** Subtitle shown below the list. Defaults to the focus-restriction note. */
  footnote?: string;
}

interface HelpSheetPropsFlat extends HelpSheetPropsBase {
  shortcuts: ShortcutEntry[];
  sections?: never;
  defaultTab?: never;
}

interface HelpSheetPropsTabbed extends HelpSheetPropsBase {
  sections: ShortcutSection[];
  /** Tab to show on open. Defaults to the first section's label. */
  defaultTab?: string;
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
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Keyboard shortcuts</DialogTitle>
          <DialogDescription className="sr-only">
            List of keyboard shortcuts available on this screen.
          </DialogDescription>
        </DialogHeader>
        {isTabbed ? (
          <TabbedBody
            sections={(props as HelpSheetPropsTabbed).sections}
            defaultTab={(props as HelpSheetPropsTabbed).defaultTab}
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

function TabbedBody({
  sections,
  defaultTab,
}: {
  sections: ShortcutSection[];
  defaultTab?: string;
}): JSX.Element {
  const tabs = sections.filter((s) => s.shortcuts.length > 0);
  const initial = defaultTab && tabs.some((t) => t.label === defaultTab)
    ? defaultTab
    : tabs[0]?.label ?? '';
  const [active, setActive] = React.useState<string>(initial);
  // Sync the active tab when the host swaps sections (e.g., route change
  // alters which feature owns "active").
  React.useEffect(() => {
    if (!tabs.some((t) => t.label === active)) {
      setActive(tabs[0]?.label ?? '');
    }
  }, [tabs, active]);

  if (tabs.length === 0) {
    return (
      <p className="text-caption text-text-muted">
        No shortcuts defined.
      </p>
    );
  }

  return (
    <Tabs value={active} onValueChange={setActive}>
      <TabsList className="flex-wrap">
        {tabs.map((s) => (
          <TabsTrigger key={s.label} value={s.label}>
            {s.label}
          </TabsTrigger>
        ))}
      </TabsList>
      {tabs.map((s) => (
        <TabsContent key={s.label} value={s.label}>
          <ShortcutList shortcuts={s.shortcuts} />
        </TabsContent>
      ))}
    </Tabs>
  );
}

export const meta = {
  name: "HelpSheet",
  layer: "pattern",
  import: "@/design-system/patterns/HelpSheet",
  variants: {},
  consumes: ["components.dialog", "components.kbd_chip"],
  example: "<HelpSheet open={false} onClose={() => {}} shortcuts={[{ keys: ['?'], description: 'Help' }]} />",
} as const;
