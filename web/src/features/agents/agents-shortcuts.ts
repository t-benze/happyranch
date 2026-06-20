/**
 * Agents page shortcut list — surfaced under the HelpDrawer "Agents" tab.
 *
 * ⌘S is the AGENTS_PAGE save shortcut — only active when the detail pane
 * form is dirty. Implemented via a keydown listener in AgentDetailPane.
 */
import type { ShortcutEntry } from '@/design-system/patterns/HelpSheet';

export const AGENTS_SHORTCUTS: ShortcutEntry[] = [
  { keys: ['g', 'g'], description: 'Jump here from anywhere' },
  { keys: ['⌘', 'S'], description: 'Save agent changes (when dirty)' },
];
