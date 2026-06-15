/**
 * Cross-cutting keyboard shortcuts surfaced under the "Global" tab of the
 * HelpDrawer. Per spec `2026-05-19-web-polish-design.md` §5 + §6.
 */
import type { ShortcutEntry } from '@/design-system/patterns/HelpSheet';

export const GLOBAL_SHORTCUTS: ShortcutEntry[] = [
  { keys: ['Cmd', 'K'], description: 'Open command palette' },
  { keys: ['?'], description: 'Show this help' },
  { keys: ['Esc'], description: 'Close any dialog, drawer, or palette' },
  { keys: ['g', 'd'], description: 'Jump to Dashboard' },
  { keys: ['g', 'i'], description: 'Jump to Threads' },
  { keys: ['g', 't'], description: 'Jump to Tasks' },
  { keys: ['g', 'k'], description: 'Jump to Knowledge Base' },
  { keys: ['g', 'l'], description: 'Jump to Threads' },
  { keys: ['g', 'a'], description: 'Jump to Audit' },
  { keys: ['g', 'g'], description: 'Jump to Agents' },
];

export const GLOBAL_SHORTCUTS_FOOTNOTE =
  'Shortcuts are suppressed when focus is inside an input, textarea, or contenteditable element.';
