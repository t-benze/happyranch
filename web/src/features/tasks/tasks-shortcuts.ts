/**
 * Tasks page shortcut list — surfaced under the HelpDrawer "Tasks" tab.
 */
import type { ShortcutEntry } from '@/design-system/patterns/HelpSheet';

export const TASKS_SHORTCUTS: ShortcutEntry[] = [
  { keys: ['g', 't'], description: 'Jump here from anywhere' },
  { keys: ['Esc'], description: 'Close the open task drawer or dialog' },
];
