/**
 * Cross-cutting keyboard shortcuts surfaced under the "Global" tab of the
 * HelpDrawer. Per spec `2026-05-19-web-polish-design.md` §5 + §6.
 *
 * THR-118 W2a: `description` values are catalog MESSAGE KEYS. The
 * `HelpDrawerHost` caller resolves them through its locale translator, keeping
 * this data file free of React/catalog-hook imports and the HelpSheet pattern
 * pure. `keys` (the actual shortcut sequences) are unchanged.
 */
import type { ShortcutEntry } from '@/design-system/patterns/HelpSheet';

export const GLOBAL_SHORTCUTS: ShortcutEntry[] = [
  { keys: ['Cmd', 'K'], description: 'help.shortcut.openAssistant' },
  { keys: ['?'], description: 'help.shortcut.showHelp' },
  { keys: ['Esc'], description: 'help.shortcut.closeAny' },
  { keys: ['g', 'd'], description: 'help.shortcut.jumpDashboard' },
  { keys: ['g', 'i'], description: 'help.shortcut.jumpThreads' },
  { keys: ['g', 't'], description: 'help.shortcut.jumpTasks' },
  { keys: ['g', 'k'], description: 'help.shortcut.jumpKnowledge' },
  { keys: ['g', 'l'], description: 'help.shortcut.jumpThreads' },
  { keys: ['g', 'a'], description: 'help.shortcut.jumpAudit' },
  { keys: ['g', 'g'], description: 'help.shortcut.jumpAgents' },
];
