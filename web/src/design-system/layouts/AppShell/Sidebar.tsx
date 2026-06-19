import { useState } from 'react';
import { Moon, Plus, Search, Settings, Sun } from 'lucide-react';
import { NavLink, useLocation, useNavigate, useParams } from 'react-router-dom';
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/design-system/primitives/Select';
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from '@/design-system/primitives/Tooltip';
import { AddOrgDialog } from '@/features/orgs/AddOrgDialog';
import { useAgentsRoutes } from '@/hooks/agents';
import { useKbRoutes } from '@/hooks/kb';
import { useOrgsList } from '@/hooks/orgs';
import { useTasksRoutes } from '@/hooks/tasks';
import { useTheme } from '@/hooks/theme';
import { useThreadRoutes } from '@/hooks/threads';
import { useGlobalJump } from '@/hooks/global-jump';
import { useOrgSlugOptional } from '@/lib/orgSlug';

/**
 * IA-1: Grouped left sidebar + desktop window chrome, retiring the ~9-tab TopBar.
 *
 * Primary group: Home, Threads, Tasks, Agents, Knowledge, Artifacts
 * Operate group: Spend, Dreams, Schedule, Audit
 * Footer: Settings (+ founder identity, theme toggle migrated from TopBar, org switcher)
 *
 * Jobs is NOT in the sidebar (still reachable via /jobs URL — retirement is P2).
 */

export function Sidebar(): JSX.Element {
  const { slug: urlSlug } = useParams<{ slug: string }>();
  const contextSlug = useOrgSlugOptional();
  const activeSlug = urlSlug ?? contextSlug ?? null;
  const navigate = useNavigate();
  const location = useLocation();
  const isPrototype = location.pathname.startsWith('/__prototypes');
  const orgsQuery = useOrgsList();
  const [addOrgOpen, setAddOrgOpen] = useState(false);
  const routes = useThreadRoutes();
  const tasksRoutes = useTasksRoutes();
  const agentsRoutes = useAgentsRoutes();
  const kbRoutes = useKbRoutes();

  // Global jump chords — reused from TopBar verbatim
  useGlobalJump('d', () => {
    if (activeSlug && !isPrototype) navigate(`/orgs/${activeSlug}/dashboard`);
  });
  useGlobalJump('i', () => {
    if (activeSlug && !isPrototype) navigate(routes.inboxForOrg(activeSlug));
  });
  useGlobalJump('t', () => {
    if (activeSlug && !isPrototype) navigate(tasksRoutes.inboxForOrg(activeSlug));
  });
  useGlobalJump('k', () => {
    if (activeSlug && !isPrototype) navigate(kbRoutes.inboxForOrg(activeSlug));
  });
  useGlobalJump('l', () => {
  });
  useGlobalJump('a', () => {
    if (activeSlug && !isPrototype) navigate(`/orgs/${activeSlug}/audit`);
  });
  useGlobalJump('g', () => {
    if (activeSlug && !isPrototype) navigate(agentsRoutes.inboxForOrg(activeSlug));
  });

  const switchEnabled = !orgsQuery.isLoading && (orgsQuery.data?.orgs.length ?? 0) > 0;

  const orgs = orgsQuery.data?.orgs ?? [];
  const currentOrg = orgs.find((o) => o.slug === activeSlug);

  const sidebarLink = (path: string, enabled: boolean) => ({
    to: enabled && activeSlug ? `/orgs/${activeSlug}/${path}` : '#',
    enabled: enabled && !!activeSlug && !isPrototype,
  });

  return (
    <aside
      role="navigation"
      aria-label="Primary navigation"
      className="border-border bg-bg-subtle flex h-full w-56 shrink-0 flex-col border-r"
    >
      {/* Brand lockup */}
      <div className="flex items-center gap-1.5 px-4 py-3">
        <svg
          viewBox="0 0 100 100"
          width="22"
          height="22"
          aria-hidden="true"
          className="shrink-0 text-[#4ade80]"
        >
          <g transform="rotate(-7 50 44)">
            <path
              d="M50 26 C68 26 78 34 78 44 C78 54 66 60 50 60 C34 60 22 54 22 44 C22 34 32 26 50 26 Z"
              fill="none"
              stroke="currentColor"
              strokeWidth="6.5"
              strokeLinecap="round"
              strokeLinejoin="round"
            />
          </g>
          <ellipse cx="41" cy="59" rx="6.2" ry="5" fill="none" stroke="currentColor" strokeWidth="5" />
          <path
            d="M44 63 C50 78 70 82 80 71 C85 65 83 59 77 60"
            fill="none"
            stroke="currentColor"
            strokeWidth="6.5"
            strokeLinecap="round"
            strokeLinejoin="round"
          />
        </svg>
        <span className="font-['Baloo_2',sans-serif] text-[1rem] leading-none font-extrabold tracking-[-0.03em]">
          <span className="text-[#4ade80]">Happy</span>
          <span className="text-fg">Ranch</span>
        </span>
      </div>

      {/* Spacer */}
      <div className="h-2" />

      {/* "Ask or search" pill — opens the global Assistant Dock */}
      <div className="px-2">
        <button
          type="button"
          data-assistant-open="true"
          className="border-border bg-bg-subtle text-fg-muted hover:border-accent-ring hover:text-fg focus-visible:ring-accent mb-2 flex w-full items-center gap-2 rounded-md border px-3 py-2 text-sm transition-colors focus-visible:ring-2 focus-visible:outline-none"
        >
          <Search size={14} aria-hidden="true" />
          <span className="flex-1 text-left">Ask or search…</span>
          <kbd className="text-fg-subtle border-border bg-bg-raised rounded border px-1.5 py-0.5 font-mono text-[10px]">
            ⌘K
          </kbd>
        </button>
      </div>

      {/* Primary group */}
      <div className="px-3">
        <SidebarGroupLabel>Primary</SidebarGroupLabel>
        <nav className="flex flex-col gap-0.5">
          <SidebarNavItem {...sidebarLink('dashboard', true)}>
            Home
          </SidebarNavItem>
          <SidebarNavItem to={routes.inboxForOrg(activeSlug ?? '')} enabled={!!activeSlug && !isPrototype}>
            Threads
          </SidebarNavItem>
          <SidebarNavItem {...sidebarLink('tasks', true)}>
            Tasks
          </SidebarNavItem>
          <SidebarNavItem {...sidebarLink('agents', true)}>
            Agents
          </SidebarNavItem>
          <SidebarNavItem {...sidebarLink('kb', true)}>
            Knowledge
          </SidebarNavItem>
          <SidebarNavItem {...sidebarLink('artifacts', true)}>
            Artifacts
          </SidebarNavItem>
        </nav>
      </div>

      {/* Operate group — IA-10 grouping */}
      <div className="mt-3 px-3">
        <SidebarGroupLabel>Operate</SidebarGroupLabel>
        <nav className="flex flex-col gap-0.5">
          <SidebarNavItem {...sidebarLink('spend', true)}>
            Spend
          </SidebarNavItem>
          <SidebarNavItem {...sidebarLink('dreams', true)}>
            Dreams
          </SidebarNavItem>
          <SidebarNavItem {...sidebarLink('schedule', true)}>
            Schedule
          </SidebarNavItem>
          <SidebarNavItem {...sidebarLink('audit', true)}>
            Audit
          </SidebarNavItem>
        </nav>
      </div>

      {/* Footer — Settings, theme toggle, org switcher, founder identity */}
      <div className="border-border mt-auto border-t px-3 py-3">
        {/* Org switcher */}
        <Select
          value={activeSlug ?? undefined}
          onValueChange={(target) => {
            if (!target || target === activeSlug) return;
            const sectionMatch = activeSlug
              ? location.pathname.match(
                  new RegExp(`^/orgs/${activeSlug}/([^/]+)`),
                )
              : null;
            const section = sectionMatch?.[1];
            navigate(section ? `/orgs/${target}/${section}` : `/orgs/${target}/dashboard`);
          }}
          disabled={!switchEnabled}
        >
          <SelectTrigger aria-label="Active org" className="w-full">
            <SelectValue placeholder="Select org…" />
          </SelectTrigger>
          <SelectContent>
            {orgs.map((o) => (
              <SelectItem key={o.slug} value={o.slug}>
                {o.slug}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>

        {/* Founder identity */}
        {currentOrg && (
          <div className="text-fg-muted mt-2 text-xs">
            Founder
          </div>
        )}

        {/* Action row: add org + settings + theme */}
        <div className="mt-2 flex items-center gap-1">
          {!isPrototype && (
            <button
              type="button"
              onClick={() => setAddOrgOpen(true)}
              aria-label="Add org"
              title="Add org"
              className="text-fg-muted hover:bg-bg-raised hover:text-fg focus-visible:ring-accent inline-flex h-7 w-7 items-center justify-center rounded transition-colors focus-visible:ring-2 focus-visible:outline-none"
            >
              <Plus size={16} aria-hidden="true" />
            </button>
          )}
          <NavLink
            to={activeSlug && !isPrototype ? `/orgs/${activeSlug}/settings` : '#'}
            aria-label="Settings"
            title="Settings"
            className={({ isActive }) =>
              `inline-flex h-7 w-7 items-center justify-center rounded transition-colors focus-visible:ring-2 focus-visible:outline-none focus-visible:ring-accent ${
                isActive
                  ? 'bg-bg-raised text-fg'
                  : 'text-fg-muted hover:bg-bg-raised hover:text-fg'
              }`
            }
          >
            <Settings size={16} aria-hidden="true" />
          </NavLink>
          <ThemeToggle />
        </div>
      </div>

      {!isPrototype && <AddOrgDialog open={addOrgOpen} onOpenChange={setAddOrgOpen} />}
    </aside>
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
      className="text-fg-muted hover:bg-bg-raised hover:text-fg focus-visible:ring-accent inline-flex h-7 w-7 items-center justify-center rounded transition-colors focus-visible:ring-2 focus-visible:outline-none"
    >
      {isDark ? <Sun size={16} aria-hidden="true" /> : <Moon size={16} aria-hidden="true" />}
    </button>
  );
}

function SidebarGroupLabel({ children }: { children: React.ReactNode }): JSX.Element {
  return (
    <div className="text-fg-subtle mb-1 px-2 text-[0.65rem] font-semibold tracking-wider uppercase">
      {children}
    </div>
  );
}

function SidebarNavItem({
  to,
  enabled,
  children,
  tooltip,
}: {
  to: string;
  enabled: boolean;
  children: React.ReactNode;
  tooltip?: string;
}): JSX.Element {
  if (!enabled) {
    const span = (
      <span
        className="text-fg-subtle cursor-not-allowed rounded px-2 py-1.5 text-sm"
        aria-disabled="true"
      >
        {children}
      </span>
    );
    if (!tooltip) return span;
    return (
      <Tooltip>
        <TooltipTrigger asChild>{span}</TooltipTrigger>
        <TooltipContent>{tooltip}</TooltipContent>
      </Tooltip>
    );
  }
  return (
    <NavLink
      to={to}
      className={({ isActive }) =>
        `rounded px-2 py-1.5 text-sm ${
          isActive
            ? 'bg-bg-raised text-fg font-medium'
            : 'text-fg-muted hover:bg-bg-raised hover:text-fg'
        }`
      }
    >
      {children}
    </NavLink>
  );
}

export const meta = {
  name: "Sidebar",
  layer: "layout",
  import: "@/design-system/layouts/AppShell/Sidebar",
  variants: {},
  consumes: ["components.sidebar", "layout.grid.app_shell"],
  example: "<Sidebar />",
} as const;
