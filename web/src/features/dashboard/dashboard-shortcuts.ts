/**
 * Dashboard page shortcut list — surfaced under the HelpDrawer "Dashboard"
 * tab. The page itself has no per-screen actions in v1; only the jump-key
 * reminder lives here.
 */
import type { ShortcutEntry } from '@/design-system/patterns/HelpSheet';

export const DASHBOARD_SHORTCUTS: ShortcutEntry[] = [
  { keys: ['g', 'd'], description: 'Jump here from anywhere' },
];
