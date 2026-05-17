/**
 * HelpSheet — keyboard-shortcut list rendered as a Dialog. Per UI_SPEC §5
 * (HelpDrawer renders as a Dialog in v0.1). Pure prop-driven.
 *
 * The shortcut list itself lives outside this pattern — the composition
 * passes it in so the same component can power a future global help sheet.
 */
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from '@/design-system/primitives/Dialog';
import { KbdChip } from './KbdChip';

export interface ShortcutEntry {
  keys: string[];
  description: string;
}

interface HelpSheetProps {
  open: boolean;
  onClose: () => void;
  shortcuts: ShortcutEntry[];
  /** Subtitle shown below the list. Defaults to the focus-restriction note. */
  footnote?: string;
}

export function HelpSheet({
  open,
  onClose,
  shortcuts,
  footnote,
}: HelpSheetProps): JSX.Element {
  return (
    <Dialog open={open} onOpenChange={(o) => { if (!o) onClose(); }}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Keyboard shortcuts</DialogTitle>
          <DialogDescription className="sr-only">
            List of keyboard shortcuts available on this screen.
          </DialogDescription>
        </DialogHeader>
        <ul className="flex flex-col gap-1.5">
          {shortcuts.map((s) => (
            <li
              key={s.keys.join('+')}
              className="flex items-center gap-3 text-body"
            >
              <span className="min-w-[5rem]">
                <KbdChip keys={s.keys} />
              </span>
              <span className="text-text-muted">{s.description}</span>
            </li>
          ))}
        </ul>
        {footnote && (
          <p className="mt-3 text-caption text-text-muted">{footnote}</p>
        )}
      </DialogContent>
    </Dialog>
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
