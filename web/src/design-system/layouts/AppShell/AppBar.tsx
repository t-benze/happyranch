import { Bot, Moon, Sun } from 'lucide-react';
import { useLocation } from 'react-router-dom';
import { useTheme } from '@/hooks/theme';

/**
 * AppBar — persistent top app bar (THR-030 BUG-04/05/06).
 *
 * Renders to the right of the Sidebar, above the routed content: current page
 * name on the left; assistant avatar + theme toggle on the right. The avatar
 * opens the global Assistant Dock via the app-wide `[data-assistant-open]`
 * click handler (AssistantDockHost) — same wiring the search pill used before
 * it was replaced by the avatar entry point (THR-056 PR-6).
 *
 * The page name is derived purely from the URL pathname (no data fetch), so it
 * stays correct on every surface without new client state.
 */

const SECTION_TITLES: Record<string, string> = {
  dashboard: 'Home',
  threads: 'Threads',
  tasks: 'Tasks',
  agents: 'Agents',
  kb: 'Knowledge',
  artifacts: 'Artifacts',
  spend: 'Spend',
  dreams: 'Dreams',
  'work-hours': 'Work Hours',
  audit: 'Audit',
  settings: 'Settings',
  jobs: 'Jobs',
  assistant: 'Assistant',
};

function pageTitleFromPath(pathname: string): string {
  const match = pathname.match(/^\/orgs\/[^/]+\/([^/]+)/);
  const section = match?.[1];
  return (section && SECTION_TITLES[section]) || 'Home';
}

export function AppBar(): JSX.Element {
  const location = useLocation();
  const title = pageTitleFromPath(location.pathname);

  return (
    <div className="border-border bg-bg-subtle flex h-12 shrink-0 items-center gap-4 border-b px-5">
      <span className="text-fg text-sm font-medium">{title}</span>
      <div className="ml-auto flex items-center gap-2">
        <button
          type="button"
          data-assistant-open="true"
          aria-label="Open assistant"
          title="Open assistant"
          className="bg-accent text-accent-fg hover:bg-accent-hover focus-visible:ring-accent inline-flex h-6 w-6 shrink-0 items-center justify-center rounded-full transition-colors focus-visible:ring-2 focus-visible:outline-none"
        >
          <Bot size={16} aria-hidden="true" />
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
      className="text-fg-muted hover:bg-bg-raised hover:text-fg focus-visible:ring-border inline-flex h-8 w-8 items-center justify-center rounded transition-colors focus-visible:ring-1 focus-visible:outline-none"
    >
      {isDark ? <Sun size={16} aria-hidden="true" /> : <Moon size={16} aria-hidden="true" />}
    </button>
  );
}

export const meta = {
  name: 'AppBar',
  layer: 'layout',
  import: '@/design-system/layouts/AppShell/AppBar',
  variants: {},
  consumes: ['components.topbar', 'layout.grid.app_shell'],
  example: '<AppBar />',
} as const;
