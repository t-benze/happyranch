/**
 * Tasks page shortcut list — surfaced under the HelpDrawer "Tasks" tab.
 *
 * `description` values are catalog MESSAGE KEYS resolved by `HelpDrawerHost`.
 */
import type { ShortcutEntry } from '@/design-system/patterns/HelpSheet';

export const TASKS_SHORTCUTS: ShortcutEntry[] = [
  { keys: ['g', 't'], description: 'help.shortcut.jumpHere' },
  { keys: ['Esc'], description: 'help.shortcut.closeTaskView' },
];
