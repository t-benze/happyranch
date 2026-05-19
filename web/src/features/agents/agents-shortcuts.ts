/**
 * Agents page shortcut list — surfaced under the HelpDrawer "Agents" tab.
 */
import type { ShortcutEntry } from '@/design-system/patterns/HelpSheet';

export const AGENTS_SHORTCUTS: ShortcutEntry[] = [
  { keys: ['g', 'g'], description: 'Jump here from anywhere' },
  { keys: ['Esc'], description: 'Close the open agent drawer' },
];
