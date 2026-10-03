/**
 * Keyboard shortcut table for the threads screen, surfaced through the
 * HelpSheet dialog (the `?` key). Kept in a small data file so the pattern
 * stays a pure function of its `shortcuts` prop.
 *
 * `description` values are catalog MESSAGE KEYS resolved by `HelpDrawerHost`.
 */
import type { ShortcutEntry } from '@/design-system/patterns/HelpSheet';

export const THREADS_SHORTCUTS: ShortcutEntry[] = [
  { keys: ['N'], description: 'help.shortcut.newThread' },
  { keys: ['I'], description: 'help.shortcut.inviteParticipant' },
  { keys: ['A'], description: 'help.shortcut.archiveThread' },
  { keys: ['F'], description: 'help.shortcut.forwardThread' },
  { keys: ['R'], description: 'help.shortcut.focusComposer' },
  { keys: ['Ctrl', 'Enter'], description: 'help.shortcut.send' },
  { keys: ['Esc'], description: 'help.shortcut.closeDialog' },
  { keys: ['?'], description: 'help.shortcut.showHelp' },
];
