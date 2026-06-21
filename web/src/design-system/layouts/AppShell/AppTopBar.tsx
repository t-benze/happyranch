import { Moon, Search, Sun } from 'lucide-react';
import { useLocation } from 'react-router-dom';
import { useTheme } from '@/hooks/theme';

/**
 * AppTopBar — persistent top app bar (THR-030 BUG-04/BUG-05/BUG-06).
 *
 * Left: the current page name (derived from the route).
 * Right: the global "Ask or search" launcher (opens the Assistant Dock, moved
 *   out of the sidebar) and the theme toggle (moved out of the sidebar footer).
 *
 * Mounts only inside the production AppShell; the prototype sandbox renders
 * its own chrome outside AppShell.
 */

const PAGE_TITLES: Record<string, string> = {
  dashboard: 'Home',
  threads: 'Threads',
  tasks: 'Tasks',
  agents: 'Agents',
  kb: 'Knowledge',
  artifacts: 'Artifacts',
  spend: 'Spend',
  dreams: 'Dreams',
  schedule: 'Schedule',
  audit: 'Audit',
  settings: 'Settings',
  jobs: 'Jobs',
  assistant: 'Assistant',
};

export function pageTitleFromPath(pathname: string): string {
  const match = pathname.match(/^\/orgs\/[^/]+\/([^/]+)/);
  const section = match?.[1];
  return (section && PAGE_TITLES[section]) ?? 'Home';
}

export function AppTopBar(): JSX.Element {
  const location = useLocation();
  const title = pageTitleFromPath(location.pathname);

  return (
    // `role="banner"` is the landmark; a <div> (not <header>) avoids
    // colliding with feature panes that locate controls via `closest('header')`.
    <div
      role="banner"
      className="border-border bg-bg-subtle flex h-12 shrink-0 items-center gap-4 border-b px-4"
    >
      {/* A label, not a document heading — each page owns its own <h1>, so
          using a heading here would create a duplicate-heading a11y issue and
          collide with page-level heading queries in tests. */}
      <span className="text-fg text-sm font-medium">{title}</span>
      <div className="ml-auto flex items-center gap-2">
        <button
          type="button"
          data-assistant-open="true"
          className="border-border bg-bg-subtle text-fg-muted hover:border-accent-ring hover:text-fg focus-visible:ring-accent flex w-64 items-center gap-2 rounded-md border px-3 py-1.5 text-sm transition-colors focus-visible:ring-2 focus-visible:outline-none"
        >
          <Search size={14} aria-hidden="true" />
          <span className="flex-1 text-left">Ask or search…</span>
          <kbd className="text-fg-subtle border-border bg-bg-raised rounded border px-1.5 py-0.5 font-mono text-[10px]">
            ⌘K
          </kbd>
        </button>
        <ThemeToggle />
      </div>
    </div>
  );
}

function ThemeToggle(): JSX.Element {
  const { theme, setTheme } = useTheme();
  const isDark = theme === 'dark';
  const label = isDark ? 'Switch to light theme' : 'Switch to dark theme';
  return (
    <button
      type="button"
      aria-label={label}
      title={label}
      onClick={() => setTheme(isDark ? 'light' : 'dark')}
      className="text-fg-muted hover:bg-bg-raised hover:text-fg focus-visible:ring-accent inline-flex h-8 w-8 items-center justify-center rounded transition-colors focus-visible:ring-2 focus-visible:outline-none"
    >
      {isDark ? <Sun size={16} aria-hidden="true" /> : <Moon size={16} aria-hidden="true" />}
    </button>
  );
}

export const meta = {
  name: 'AppTopBar',
  layer: 'layout',
  import: '@/design-system/layouts/AppShell/AppTopBar',
  variants: {},
  consumes: ['components.topbar', 'layout.grid.app_shell'],
  example: '<AppTopBar />',
} as const;
