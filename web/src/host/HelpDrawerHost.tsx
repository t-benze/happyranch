/**
 * HelpDrawerHost — single instance of the tabbed HelpDrawer mounted in the
 * AppShell. Listens for `?` globally (suppressed when focus is inside an
 * editable element) and feeds every feature's shortcut list into the
 * `HelpSheet` pattern as a `sections` prop.
 *
 * THR-118 W2a: section labels are localized and resolved through the W1
 * catalog, while the TAB IDENTITY is the stable `id` below — never the
 * localized label. A language switch re-renders the copy but preserves the
 * selected tab, the open state and every shortcut key sequence. Shortcut
 * `description` values in the `*-shortcuts.ts` data files are catalog keys
 * translated here in the caller; the HelpSheet pattern stays pure/prop-driven.
 *
 * Per spec `2026-05-19-web-polish-design.md` §7.
 */
import * as React from 'react';
import { useLocation } from 'react-router-dom';

import {
  HelpSheet,
  type ShortcutEntry,
  type ShortcutSection,
} from '@/design-system/patterns/HelpSheet';
import { AGENTS_SHORTCUTS } from '@/features/agents/agents-shortcuts';
import { AUDIT_SHORTCUTS } from '@/features/audit/audit-shortcuts';
import { DASHBOARD_SHORTCUTS } from '@/features/dashboard/dashboard-shortcuts';
import { KB_SHORTCUTS } from '@/features/kb/kb-shortcuts';
import { TASKS_SHORTCUTS } from '@/features/tasks/tasks-shortcuts';
import { THREADS_SHORTCUTS } from '@/features/threads/threads-shortcuts';
import { useTranslation } from '@/hooks/i18n';
import type { MessageKey } from '@/lib/i18n';
import { GLOBAL_SHORTCUTS } from './global-shortcuts';

interface SectionDefinition {
  /** Stable tab identity — never localized. */
  id: string;
  labelKey: MessageKey;
  shortcuts: ShortcutEntry[];
}

const SECTION_DEFINITIONS: readonly SectionDefinition[] = [
  { id: 'global', labelKey: 'help.tab.global', shortcuts: GLOBAL_SHORTCUTS },
  { id: 'dashboard', labelKey: 'help.tab.dashboard', shortcuts: DASHBOARD_SHORTCUTS },
  { id: 'threads', labelKey: 'help.tab.threads', shortcuts: THREADS_SHORTCUTS },
  { id: 'tasks', labelKey: 'help.tab.tasks', shortcuts: TASKS_SHORTCUTS },
  { id: 'kb', labelKey: 'help.tab.kb', shortcuts: KB_SHORTCUTS },
  { id: 'agents', labelKey: 'help.tab.agents', shortcuts: AGENTS_SHORTCUTS },
  { id: 'audit', labelKey: 'help.tab.audit', shortcuts: AUDIT_SHORTCUTS },
];

function isInEditable(target: EventTarget | null): boolean {
  if (!(target instanceof HTMLElement)) return false;
  const tag = target.tagName;
  if (tag === 'INPUT' || tag === 'TEXTAREA') return true;
  if (target.isContentEditable) return true;
  return false;
}

/** Stable tab id for the current route (matches `SECTION_DEFINITIONS.id`). */
export function defaultTabForRoute(pathname: string): string {
  if (pathname.includes('/dashboard')) return 'dashboard';
  if (pathname.includes('/threads')) return 'threads';
  if (pathname.includes('/tasks')) return 'tasks';
  if (pathname.includes('/kb')) return 'kb';
  if (pathname.includes('/agents')) return 'agents';
  if (pathname.includes('/audit')) return 'audit';
  return 'global';
}

export function HelpDrawerHost(): JSX.Element {
  const [open, setOpen] = React.useState(false);
  const location = useLocation();
  const { t } = useTranslation();

  React.useEffect(() => {
    const handler = (ev: KeyboardEvent) => {
      if (ev.key !== '?') return;
      if (isInEditable(ev.target)) return;
      ev.preventDefault();
      setOpen((o) => !o);
    };
    window.addEventListener('keydown', handler);
    return () => window.removeEventListener('keydown', handler);
  }, []);

  const sections = React.useMemo<ShortcutSection[]>(
    () =>
      SECTION_DEFINITIONS.map((definition) => ({
        id: definition.id,
        label: t(definition.labelKey),
        shortcuts: definition.shortcuts.map((shortcut) => ({
          keys: shortcut.keys,
          description: t(shortcut.description as MessageKey),
        })),
      })),
    [t],
  );

  return (
    <HelpSheet
      open={open}
      onClose={() => setOpen(false)}
      sections={sections}
      defaultTabId={defaultTabForRoute(location.pathname)}
      title={t('help.title')}
      description={t('help.description')}
      emptyLabel={t('help.empty')}
      footnote={t('help.footnote')}
      closeLabel={t('common.close')}
    />
  );
}
