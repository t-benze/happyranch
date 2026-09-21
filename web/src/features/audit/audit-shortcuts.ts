/**
 * Audit page shortcut list — surfaced under the HelpDrawer "Audit" tab.
 *
 * `description` values are catalog MESSAGE KEYS resolved by `HelpDrawerHost`.
 */
import type { ShortcutEntry } from '@/design-system/patterns/HelpSheet';

export const AUDIT_SHORTCUTS: ShortcutEntry[] = [
  { keys: ['g', 'a'], description: 'help.shortcut.jumpHere' },
];
