import { useState } from 'react';
import * as SelectPrimitive from '@radix-ui/react-select';
import {
  BookOpen,
  Calendar,
  ChevronDown,
  type LucideIcon,
  ListChecks,
  MessageSquare,
  Package,
  ScrollText,
  Settings,
  Sparkles,
  Users,
  Wallet,
  Home as HomeIcon,
} from 'lucide-react';
import { NavLink, useLocation, useNavigate, useParams } from 'react-router-dom';
import {
  SelectContent,
  SelectItem,
  SelectSeparator,
} from '@/design-system/primitives/Select';
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from '@/design-system/primitives/Tooltip';
import { AddOrgDialog } from '@/features/orgs/AddOrgDialog';
import { useAgentsRoutes } from '@/hooks/agents';
import { useDashboardSummary } from '@/hooks/dashboard';
import { useKbRoutes } from '@/hooks/kb';
import { useOrgsList } from '@/hooks/orgs';
import { useTasksRoutes } from '@/hooks/tasks';
import { useThreadRoutes } from '@/hooks/threads';
import { useGlobalJump } from '@/hooks/global-jump';
import { useOrgSlugOptional } from '@/lib/orgSlug';

/**
 * IA-1: Grouped left sidebar + desktop window chrome, retiring the ~9-tab TopBar.
 *
 * THR-030 chrome alignment (BUG-01..08):
 *  - Top: context header (wordmark + org context line + caret) doubling as the
 *    org switcher — restyled from the native footer `<select>` (BUG-01/08).
 *  - Primary group: Home, Threads, Tasks, Agents, Knowledge, Artifacts — each
 *    with a leading icon (BUG-03).
 *  - Operate group: Spend, Dreams, Schedule, Audit.
 *  - Footer: Settings as a labeled row above the account row (BUG-02), then an
 *    avatar + identity account row (BUG-07).
 *
 * Global search and the theme toggle moved to the AppBar (BUG-04/05/06).
 *
 * Jobs is NOT in the sidebar (still reachable via /jobs URL — retirement is P2).
 */

const ADD_ORG_VALUE = '__add_org__';

// BUG-03: nav badges show REAL counts from the shared dashboard summary
// (Agents <- agents_active_now, Audit <- escalated_open). A badge renders only
// for a positive, finite count; 0 / undefined / NaN render no badge (no "0"
// noise). Threads/Tasks/Dreams have no backing field in narrative_counts, so
// they stay badge-less — wiring a count there would need a new daemon/SQLite
// data path, out of this presentation-only scope.
function positiveCount(value: number | undefined): number | undefined {
  return typeof value === 'number' && Number.isFinite(value) && value > 0
    ? value
    : undefined;
}

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

  // BUG-08/BUG-03: Day-N + nav counts come from the SAME dashboard-summary
  // query DashboardPage consumes — React Query dedupes by key, so this is not a
  // new data path. The hook self-gates on the active org slug, so non-org
  // routes issue no fetch.
  const summary = useDashboardSummary().data;
  const orgAgeDays = summary?.org_age_days;
  const counts = summary?.narrative_counts;

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

  const sidebarLink = (path: string, enabled: boolean) => ({
    to: enabled && activeSlug ? `/orgs/${activeSlug}/${path}` : '#',
    enabled: enabled && !!activeSlug && !isPrototype,
  });

  const onOrgChange = (target: string): void => {
    if (target === ADD_ORG_VALUE) {
      setAddOrgOpen(true);
      return;
    }
    if (!target || target === activeSlug) return;
    // Stay on the same section; strip any detail suffix so we land on the
    // section inbox, not a stale detail route.
    const sectionMatch = activeSlug
      ? location.pathname.match(new RegExp(`^/orgs/${activeSlug}/([^/]+)`))
      : null;
    const section = sectionMatch?.[1];
    navigate(section ? `/orgs/${target}/${section}` : `/orgs/${target}/dashboard`);
  };

  return (
    <aside
      role="navigation"
      aria-label="Primary navigation"
      className="border-border bg-bg-subtle w-rail flex h-full shrink-0 flex-col border-r"
    >
      {/* Context header — wordmark + org context line + caret, doubling as the
          org switcher (BUG-01/08). Keeps the existing org-switch route logic. */}
      <SelectPrimitive.Root
        value={activeSlug ?? undefined}
        onValueChange={onOrgChange}
        disabled={!switchEnabled}
      >
        <SelectPrimitive.Trigger asChild aria-label="Active org">
          <button
            type="button"
            className="border-border hover:bg-bg-raised focus-visible:ring-accent flex w-full items-center gap-2 border-b px-4 py-3 text-left transition-colors focus-visible:ring-2 focus-visible:outline-none disabled:cursor-not-allowed"
          >
            <Brandmark />
            <span className="flex min-w-0 flex-1 flex-col">
              <span className="font-['Baloo_2',sans-serif] text-[1rem] leading-tight font-extrabold tracking-[-0.03em]">
                <span className="text-[#4ade80]">Happy</span>
                <span className="text-fg">Ranch</span>
              </span>
              {/* Context line — "Day N · <team>" (BUG-08). Day-N is the real
                  org_age_days from the dashboard summary; the team is the active
                  org slug. On a brand-new org (org_age_days 0/undefined) the day
                  token degrades away, leaving the bare slug — never "Day 0". */}
              <span className="text-fg-subtle truncate text-[0.7rem] leading-tight">
                {activeSlug ? (
                  orgAgeDays && orgAgeDays > 0 ? (
                    <>
                      <span className="text-fg-muted">Day {orgAgeDays}</span> ·{' '}
                      <span>{activeSlug}</span>
                    </>
                  ) : (
                    <span>{activeSlug}</span>
                  )
                ) : (
                  'No org'
                )}
              </span>
            </span>
            <ChevronDown size={14} aria-hidden="true" className="text-fg-muted shrink-0" />
          </button>
        </SelectPrimitive.Trigger>
        <SelectContent>
          {orgs.map((o) => (
            <SelectItem key={o.slug} value={o.slug}>
              {o.slug}
            </SelectItem>
          ))}
          {!isPrototype && (
            <>
              <SelectSeparator />
              <SelectItem value={ADD_ORG_VALUE}>+ Add org…</SelectItem>
            </>
          )}
        </SelectContent>
      </SelectPrimitive.Root>

      {/* Primary group */}
      <div className="mt-3 px-3">
        <SidebarGroupLabel>Primary</SidebarGroupLabel>
        <nav className="flex flex-col gap-0.5">
          <SidebarNavItem {...sidebarLink('dashboard', true)} icon={HomeIcon}>
            Home
          </SidebarNavItem>
          <SidebarNavItem
            to={routes.inboxForOrg(activeSlug ?? '')}
            enabled={!!activeSlug && !isPrototype}
            icon={MessageSquare}
          >
            Threads
          </SidebarNavItem>
          <SidebarNavItem {...sidebarLink('tasks', true)} icon={ListChecks}>
            Tasks
          </SidebarNavItem>
          <SidebarNavItem
            {...sidebarLink('agents', true)}
            icon={Users}
            badge={positiveCount(counts?.agents_active_now)}
          >
            Agents
          </SidebarNavItem>
          <SidebarNavItem {...sidebarLink('kb', true)} icon={BookOpen}>
            Knowledge
          </SidebarNavItem>
          <SidebarNavItem {...sidebarLink('artifacts', true)} icon={Package}>
            Artifacts
          </SidebarNavItem>
        </nav>
      </div>

      {/* Operate group — IA-10 grouping */}
      <div className="mt-3 px-3">
        <SidebarGroupLabel>Operate</SidebarGroupLabel>
        <nav className="flex flex-col gap-0.5">
          <SidebarNavItem {...sidebarLink('spend', true)} icon={Wallet}>
            Spend
          </SidebarNavItem>
          <SidebarNavItem {...sidebarLink('dreams', true)} icon={Sparkles}>
            Dreams
          </SidebarNavItem>
          <SidebarNavItem {...sidebarLink('schedule', true)} icon={Calendar}>
            Schedule
          </SidebarNavItem>
          <SidebarNavItem
            {...sidebarLink('audit', true)}
            icon={ScrollText}
            badge={positiveCount(counts?.escalated_open)}
          >
            Audit
          </SidebarNavItem>
        </nav>
      </div>

      {/* Footer — Settings labeled row (BUG-02) + account row (BUG-07) */}
      <div className="border-border mt-auto flex flex-col gap-1 border-t px-3 py-3">
        <NavLink
          to={activeSlug && !isPrototype ? `/orgs/${activeSlug}/settings` : '#'}
          className={({ isActive }) =>
            `flex items-center gap-2.5 rounded px-2 py-1.5 text-sm transition-colors focus-visible:ring-2 focus-visible:outline-none focus-visible:ring-accent ${
              isActive
                ? 'bg-bg-raised text-fg font-medium'
                : 'text-fg-muted hover:bg-bg-raised hover:text-fg'
            }`
          }
        >
          <Settings size={16} aria-hidden="true" className="shrink-0" />
          <span>Settings</span>
        </NavLink>

        {/* Account row — avatar + identity. Identity is static chrome (no user
            profile is loaded client-side). */}
        <div className="flex items-center gap-2.5 px-2 py-1.5">
          <span
            aria-hidden="true"
            className="bg-accent text-accent-fg inline-flex h-6 w-6 shrink-0 items-center justify-center rounded-full text-[0.65rem] font-semibold"
          >
            YT
          </span>
          <span className="flex min-w-0 flex-col leading-tight">
            <span className="text-fg truncate text-sm">You</span>
            <span className="text-fg-subtle truncate text-xs">Founder</span>
          </span>
        </div>
      </div>

      {!isPrototype && <AddOrgDialog open={addOrgOpen} onOpenChange={setAddOrgOpen} />}
    </aside>
  );
}

function Brandmark(): JSX.Element {
  return (
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
  );
}

function SidebarGroupLabel({ children }: { children: React.ReactNode }): JSX.Element {
  return (
    <div className="text-fg-subtle mb-1 px-2 text-[0.65rem] font-semibold tracking-wider uppercase">
      {children}
    </div>
  );
}

function NavCountBadge({ value }: { value: number }): JSX.Element {
  return (
    <span
      data-testid="nav-count-badge"
      aria-hidden="true"
      className="bg-bg-raised text-fg-subtle ml-auto inline-flex min-w-[1.25rem] items-center justify-center rounded-full px-1.5 py-0.5 text-[0.65rem] font-medium tabular-nums"
    >
      {value}
    </span>
  );
}

function SidebarNavItem({
  to,
  enabled,
  children,
  icon: Icon,
  tooltip,
  badge,
}: {
  to: string;
  enabled: boolean;
  children: React.ReactNode;
  icon: LucideIcon;
  tooltip?: string;
  badge?: number;
}): JSX.Element {
  if (!enabled) {
    const span = (
      <span
        className="text-fg-subtle flex cursor-not-allowed items-center gap-2.5 rounded px-2 py-1.5 text-sm"
        aria-disabled="true"
      >
        <Icon size={16} aria-hidden="true" className="shrink-0" />
        <span>{children}</span>
        {badge !== undefined && <NavCountBadge value={badge} />}
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
        `flex items-center gap-2.5 rounded px-2 py-1.5 text-sm ${
          isActive
            ? 'bg-bg-raised text-fg font-medium'
            : 'text-fg-muted hover:bg-bg-raised hover:text-fg'
        }`
      }
    >
      <Icon size={16} aria-hidden="true" className="shrink-0" />
      <span>{children}</span>
      {badge !== undefined && <NavCountBadge value={badge} />}
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
