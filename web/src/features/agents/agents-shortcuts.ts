/**
 * Agents page shortcut list — surfaced under the HelpDrawer "Agents" tab.
 *
 * ⌘S is the AGENTS_PAGE save shortcut — only active when the detail pane
 * form is dirty. Implemented via a keydown listener in AgentDetailPane.
 *
 * `description` values are catalog MESSAGE KEYS resolved by `HelpDrawerHost`.
 */
import type { ShortcutEntry } from '@/design-system/patterns/HelpSheet';

export const AGENTS_SHORTCUTS: ShortcutEntry[] = [
  { keys: ['g', 'g'], description: 'help.shortcut.jumpHere' },
  { keys: ['⌘', 'S'], description: 'help.shortcut.saveAgent' },
];
