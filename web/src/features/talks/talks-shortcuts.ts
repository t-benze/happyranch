/**
 * Talks page shortcut list — surfaced under the HelpDrawer "Talks" tab.
 */
import type { ShortcutEntry } from '@/design-system/patterns/HelpSheet';

export const TALKS_SHORTCUTS: ShortcutEntry[] = [
  { keys: ['g', 'l'], description: 'Jump here from anywhere' },
  { keys: ['N'], description: 'Start a new talk' },
  { keys: ['E'], description: 'End the open talk' },
  { keys: ['X'], description: 'Abandon the open talk' },
  { keys: ['D'], description: 'Dispatch a task from the talk' },
];
