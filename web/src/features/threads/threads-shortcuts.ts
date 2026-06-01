/**
 * Keyboard shortcut table for the threads screen, surfaced through the
 * HelpSheet dialog (the `?` key). Kept in a small data file so the pattern
 * stays a pure function of its `shortcuts` prop.
 */
import type { ShortcutEntry } from '@/design-system/patterns/HelpSheet';

export const THREADS_SHORTCUTS: ShortcutEntry[] = [
  { keys: ['N'], description: 'New thread' },
  { keys: ['I'], description: 'Invite participant' },
  { keys: ['A'], description: 'Archive thread' },
  { keys: ['F'], description: 'Forward thread (compose new with quoted excerpt)' },
  { keys: ['R'], description: 'Focus composer' },
  { keys: ['Ctrl', 'Enter'], description: 'Send (in composer)' },
  { keys: ['Esc'], description: 'Close dialog' },
  { keys: ['?'], description: 'Show this help' },
];

export const THREADS_SHORTCUTS_FOOTNOTE =
  'Shortcuts are disabled while focus is inside an input or textarea.';
