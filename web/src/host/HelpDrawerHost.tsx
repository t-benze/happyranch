/**
 * HelpDrawerHost — single instance of the tabbed HelpDrawer mounted in the
 * AppShell. Listens for `?` globally (suppressed when focus is inside an
 * editable element) and feeds every feature's shortcut list into the
 * `HelpSheet` pattern as a `sections` prop.
 *
 * Per spec `2026-05-19-web-polish-design.md` §7.
 */
import * as React from 'react';
import { useLocation } from 'react-router-dom';

import {
  HelpSheet,
  type ShortcutSection,
} from '@/design-system/patterns/HelpSheet';
import { AGENTS_SHORTCUTS } from '@/features/agents/agents-shortcuts';
import { AUDIT_SHORTCUTS } from '@/features/audit/audit-shortcuts';
import { DASHBOARD_SHORTCUTS } from '@/features/dashboard/dashboard-shortcuts';
import { KB_SHORTCUTS } from '@/features/kb/kb-shortcuts';
import { TALKS_SHORTCUTS } from '@/features/talks/talks-shortcuts';
import { TASKS_SHORTCUTS } from '@/features/tasks/tasks-shortcuts';
import { THREADS_SHORTCUTS } from '@/features/threads/threads-shortcuts';
import {
  GLOBAL_SHORTCUTS,
  GLOBAL_SHORTCUTS_FOOTNOTE,
} from './global-shortcuts';

const SECTIONS: ShortcutSection[] = [
  { label: 'Global', shortcuts: GLOBAL_SHORTCUTS },
  { label: 'Dashboard', shortcuts: DASHBOARD_SHORTCUTS },
  { label: 'Threads', shortcuts: THREADS_SHORTCUTS },
  { label: 'Tasks', shortcuts: TASKS_SHORTCUTS },
  { label: 'KB', shortcuts: KB_SHORTCUTS },
  { label: 'Agents', shortcuts: AGENTS_SHORTCUTS },
  { label: 'Audit', shortcuts: AUDIT_SHORTCUTS },
  { label: 'Talks', shortcuts: TALKS_SHORTCUTS },
];

function isInEditable(target: EventTarget | null): boolean {
  if (!(target instanceof HTMLElement)) return false;
  const tag = target.tagName;
  if (tag === 'INPUT' || tag === 'TEXTAREA') return true;
  if (target.isContentEditable) return true;
  return false;
}

function defaultTabForRoute(pathname: string): string {
  if (pathname.includes('/dashboard')) return 'Dashboard';
  if (pathname.includes('/threads')) return 'Threads';
  if (pathname.includes('/tasks')) return 'Tasks';
  if (pathname.includes('/kb')) return 'KB';
  if (pathname.includes('/agents')) return 'Agents';
  if (pathname.includes('/audit')) return 'Audit';
  if (pathname.includes('/talks')) return 'Talks';
  return 'Global';
}

export function HelpDrawerHost(): JSX.Element {
  const [open, setOpen] = React.useState(false);
  const location = useLocation();

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

  return (
    <HelpSheet
      open={open}
      onClose={() => setOpen(false)}
      sections={SECTIONS}
      defaultTab={defaultTabForRoute(location.pathname)}
      footnote={GLOBAL_SHORTCUTS_FOOTNOTE}
    />
  );
}
