/**
 * KB page shortcut list — surfaced under the HelpDrawer "KB" tab.
 *
 * `description` values are catalog MESSAGE KEYS resolved by `HelpDrawerHost`.
 */
import type { ShortcutEntry } from '@/design-system/patterns/HelpSheet';

export const KB_SHORTCUTS: ShortcutEntry[] = [
  { keys: ['g', 'k'], description: 'help.shortcut.jumpHere' },
];
