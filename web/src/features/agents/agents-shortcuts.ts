/**
 * Agents page shortcut list — surfaced under the HelpDrawer "Agents" tab.
 *
 * ⌘S is the AGENTS_PAGE save shortcut — only active when the detail pane
 * form is dirty. Implemented via a keydown listener in AgentDetailPane.
 */
import type { ShortcutEntry } from '@/design-system/patterns/HelpSheet';

export const AGENTS_SHORTCUTS: ShortcutEntry[] = [
  { keys: ['g', 'g'], description: 'Jump here from anywhere' },
  { keys: ['↑', '↓'], description: 'Navigate agent roster' },
  { keys: ['Enter'], description: 'Open selected agent detail' },
  { keys: ['⌘', 'S'], description: 'Save agent changes (when dirty)' },
  { keys: ['Esc'], description: 'Close the selected agent detail' },
];
