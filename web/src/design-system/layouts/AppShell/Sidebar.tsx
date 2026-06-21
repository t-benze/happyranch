import { useState } from 'react';
import {
  BookOpen,
  CalendarClock,
  ChevronsUpDown,
  ClipboardList,
  Home,
  ListTodo,
  Moon,
  Package,
  Plus,
  Receipt,
  type LucideIcon,
  MessagesSquare,
  Settings,
  Users,
} from 'lucide-react';
import { NavLink, useLocation, useNavigate, useParams } from 'react-router-dom';
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from '@/design-system/primitives/DropdownMenu';
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
import { useThreadRoutes } from '@/hooks/threads';
import { useGlobalJump } from '@/hooks/global-jump';
import { useOrgSlugOptional } from '@/lib/orgSlug';

/**
 * IA-1: Grouped left sidebar + desktop window chrome, retiring the ~9-tab TopBar.
 *
 * Header: context switcher (wordmark + "<team>" line + caret) — the org
 *   switcher, restyled as a context header (THR-030 BUG-01/BUG-08). It keeps
 *   the existing org-switch navigation logic; only the trigger widget changed
 *   from a native-style <Select> combobox to a context-header DropdownMenu.
 * Primary group: Home, Threads, Tasks, Agents, Knowledge, Artifacts
 * Operate group: Spend, Dreams, Schedule, Audit
 * Footer: Settings (labeled row + gear) above the account identity row.
 *
 * Global search and the theme toggle live in the top app bar (AppTopBar), not
 * here (THR-030 BUG-04/BUG-05/BUG-06).
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

  // Existing org-switch route logic, preserved verbatim from the prior <Select>
  // onValueChange (THR-030 wiring constraint: no auth/permission/schema change).
  const switchOrg = (target: string) => {
    if (!target || target === activeSlug) return;
    const sectionMatch = activeSlug
      ? location.pathname.match(new RegExp(`^/orgs/${activeSlug}/([^/]+)`))
      : null;
    const section = sectionMatch?.[1];
    navigate(section ? `/orgs/${target}/${section}` : `/orgs/${target}/dashboard`);
  };

  const sidebarLink = (path: string, enabled: boolean) => ({
    to: enabled && activeSlug ? `/orgs/${activeSlug}/${path}` : '#',
    enabled: enabled && !!activeSlug && !isPrototype,
  });

  return (
    <aside
      role="navigation"
      aria-label="Primary navigation"
      className="border-border bg-bg-subtle w-rail flex h-full shrink-0 flex-col border-r"
    >
      {/* Context switcher header — wordmark + context line + caret (BUG-01/08) */}
      <div className="border-border border-b px-2 py-2">
        <DropdownMenu>
          <DropdownMenuTrigger
            aria-label="Active org"
            disabled={!switchEnabled}
            className="hover:bg-bg-raised focus-visible:ring-accent flex w-full items-center gap-2 rounded-md px-2 py-1.5 text-left transition-colors focus-visible:ring-2 focus-visible:outline-none disabled:cursor-default disabled:opacity-60"
          >
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
            <span className="min-w-0 flex-1">
              <span className="block font-['Baloo_2',sans-serif] text-[1rem] leading-none font-extrabold tracking-[-0.03em]">
                <span className="text-[#4ade80]">Happy</span>
                <span className="text-fg">Ranch</span>
              </span>
              {activeSlug && (
                <span className="text-fg-muted mt-0.5 block truncate text-xs">
                  {activeSlug}
                </span>
              )}
            </span>
            <ChevronsUpDown size={14} aria-hidden="true" className="text-fg-subtle shrink-0" />
          </DropdownMenuTrigger>
          <DropdownMenuContent align="start" className="min-w-48">
            {orgs.map((o) => (
              <DropdownMenuItem
                key={o.slug}
                onSelect={() => switchOrg(o.slug)}
                className={o.slug === activeSlug ? 'text-fg font-medium' : 'text-fg-muted'}
              >
                {o.slug}
              </DropdownMenuItem>
            ))}
            {!isPrototype && (
              <>
                <DropdownMenuSeparator />
                <DropdownMenuItem onSelect={() => setAddOrgOpen(true)}>
                  <Plus size={14} aria-hidden="true" />
                  Add org…
                </DropdownMenuItem>
              </>
            )}
          </DropdownMenuContent>
        </DropdownMenu>
      </div>

      {/* Primary group */}
      <div className="mt-2 px-3">
        <SidebarGroupLabel>Primary</SidebarGroupLabel>
        <nav className="flex flex-col gap-0.5">
          <SidebarNavItem icon={Home} {...sidebarLink('dashboard', true)}>
            Home
          </SidebarNavItem>
          <SidebarNavItem
            icon={MessagesSquare}
            to={routes.inboxForOrg(activeSlug ?? '')}
            enabled={!!activeSlug && !isPrototype}
          >
            Threads
          </SidebarNavItem>
          <SidebarNavItem icon={ListTodo} {...sidebarLink('tasks', true)}>
            Tasks
          </SidebarNavItem>
          <SidebarNavItem icon={Users} {...sidebarLink('agents', true)}>
            Agents
          </SidebarNavItem>
          <SidebarNavItem icon={BookOpen} {...sidebarLink('kb', true)}>
            Knowledge
          </SidebarNavItem>
          <SidebarNavItem icon={Package} {...sidebarLink('artifacts', true)}>
            Artifacts
          </SidebarNavItem>
        </nav>
      </div>

      {/* Operate group — IA-10 grouping */}
      <div className="mt-3 px-3">
        <SidebarGroupLabel>Operate</SidebarGroupLabel>
        <nav className="flex flex-col gap-0.5">
          <SidebarNavItem icon={Receipt} {...sidebarLink('spend', true)}>
            Spend
          </SidebarNavItem>
          <SidebarNavItem icon={Moon} {...sidebarLink('dreams', true)}>
            Dreams
          </SidebarNavItem>
          <SidebarNavItem icon={CalendarClock} {...sidebarLink('schedule', true)}>
            Schedule
          </SidebarNavItem>
          <SidebarNavItem icon={ClipboardList} {...sidebarLink('audit', true)}>
            Audit
          </SidebarNavItem>
        </nav>
      </div>

      {/* Footer — Settings labeled row above the account identity row */}
      <div className="border-border mt-auto border-t px-3 py-3">
        <NavLink
          to={activeSlug && !isPrototype ? `/orgs/${activeSlug}/settings` : '#'}
          aria-label="Settings"
          className={({ isActive }) =>
            `flex items-center gap-2 rounded px-2 py-1.5 text-sm transition-colors ${
              isActive
                ? 'bg-bg-raised text-fg font-medium'
                : 'text-fg-muted hover:bg-bg-raised hover:text-fg'
            }`
          }
        >
          <Settings size={16} aria-hidden="true" className="shrink-0" />
          <span>Settings</span>
        </NavLink>

        {/* Account row — avatar + identity (BUG-07) */}
        <div className="mt-1 flex items-center gap-2 px-2 py-1.5">
          <span
            aria-hidden="true"
            className="inline-flex h-7 w-7 shrink-0 items-center justify-center rounded-full bg-[#4ade80] text-[0.65rem] font-bold text-[#0a0a0a]"
          >
            YT
          </span>
          <span className="min-w-0 leading-tight">
            <span className="text-fg block text-sm font-medium">You</span>
            <span className="text-fg-muted block text-xs">Founder</span>
          </span>
        </div>
      </div>

      {!isPrototype && <AddOrgDialog open={addOrgOpen} onOpenChange={setAddOrgOpen} />}
    </aside>
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
  icon: Icon,
  badge,
  children,
  tooltip,
}: {
  to: string;
  enabled: boolean;
  icon: LucideIcon;
  /**
   * Optional count badge (BUG-03). Rendered only when a positive count is
   * supplied. Counts are NOT wired in this batch — binding them would require
   * adding cross-surface data fetches to the global chrome (escalated to the
   * engineering_manager per the THR-030 presentation-only wiring constraint).
   */
  badge?: number;
  children: React.ReactNode;
  tooltip?: string;
}): JSX.Element {
  const showBadge = typeof badge === 'number' && badge > 0;
  if (!enabled) {
    const span = (
      <span
        className="text-fg-subtle flex cursor-not-allowed items-center gap-2 rounded px-2 py-1.5 text-sm"
        aria-disabled="true"
      >
        <Icon size={16} aria-hidden="true" className="shrink-0" />
        <span className="flex-1">{children}</span>
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
        `flex items-center gap-2 rounded px-2 py-1.5 text-sm ${
          isActive
            ? 'bg-bg-raised text-fg font-medium'
            : 'text-fg-muted hover:bg-bg-raised hover:text-fg'
        }`
      }
    >
      <Icon size={16} aria-hidden="true" className="shrink-0" />
      <span className="flex-1">{children}</span>
      {showBadge && (
        <span className="bg-bg-raised text-fg-muted inline-flex min-w-5 items-center justify-center rounded-full px-1.5 text-[10px] font-medium tabular-nums">
          {badge}
        </span>
      )}
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
