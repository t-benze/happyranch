import { useState } from 'react';
import * as SelectPrimitive from '@radix-ui/react-select';
import {
  Activity,
  BookOpen,
  ChevronDown,
  Clock,
  type LucideIcon,
  ListChecks,
  MessageSquare,
  Package,
  Puzzle,
  ScrollText,
  Settings,
  Sparkles,
  Terminal,
  Users,
  Wallet,
  Workflow,
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
import { useDashboardSummary } from '@/hooks/dashboard';
import { useTranslation } from '@/hooks/i18n';
import { identityInitials, useFounderName, useHasIdentityProvider } from '@/hooks/identities';
import { Avatar } from '@/design-system/primitives/Avatar';

import { useOrgsList } from '@/hooks/orgs';
import { useThreadRoutes } from '@/hooks/threads';
import { useOrgSlugOptional } from '@/lib/orgSlug';
import { features } from '@/config/features';

/**
 * IA-1: Grouped left sidebar + desktop window chrome, retiring the legacy tab bar.
 *
 * THR-030 chrome alignment (BUG-01..08):
 *  - Top: context header (wordmark + org context line + caret) doubling as the
 *    org switcher — restyled from the native footer `<select>` (BUG-01/08).
 *  - Primary navigation: one flat, usage-ordered list of Home, Threads, Tasks,
 *    Jobs, Todos, Agents, Work Hours, Skills, Knowledge, Artifacts, Audit,
 *    Dreams, Usage, and Health.
 *  - Footer: Settings is permanently pinned above the static avatar + identity
 *    account row (BUG-07), separated from the primary list by the footer divider.
 *
 * Global search and the theme toggle moved to the AppBar (BUG-04/05/06).
 *
 * Jobs is reinstated as the approval queue per founder ruling (THR-030 seq 91,
 * TASK-907).
 */

const ADD_ORG_VALUE = '__add_org__';

export function Sidebar(): JSX.Element {
  const { slug: urlSlug } = useParams<{ slug: string }>();
  const contextSlug = useOrgSlugOptional();
  const activeSlug = urlSlug ?? contextSlug ?? null;
  const navigate = useNavigate();
  const location = useLocation();
  const { t } = useTranslation();
  const isPrototype = location.pathname.startsWith('/__prototypes');
  const orgsQuery = useOrgsList();
  const [addOrgOpen, setAddOrgOpen] = useState(false);
  const routes = useThreadRoutes();

  // BUG-08: Day-N comes from the SAME dashboard-summary query DashboardPage
  // consumes — React Query dedupes by key, so this is not a new data path.
  // The hook self-gates on the active org slug, so non-org routes issue no
  // fetch.
  const summary = useDashboardSummary().data;
  const orgAgeDays = summary?.org_age_days;

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
    // Threads stays mounted for same-section org changes. Let its route-local
    // list capture the outgoing ready owner before this navigation can reuse
    // it for an uncached org's loading content.
    window.dispatchEvent(new Event('threads:before-org-change'));
    navigate(section ? `/orgs/${target}/${section}` : `/orgs/${target}/dashboard`);
  };

  return (
    <aside
      role="navigation"
      aria-label={t('shell.nav.primary')}
      className="border-border-default bg-surface-sunken w-rail-narrow md:w-rail flex h-full shrink-0 flex-col border-r"
    >
      {/* Context header — wordmark + org context line + caret, doubling as the
          org switcher (BUG-01/08). Keeps the existing org-switch route logic. */}
      <section aria-label={t('shell.orgSwitcher')} className="shrink-0">
        <SelectPrimitive.Root
          value={activeSlug ?? undefined}
          onValueChange={onOrgChange}
          disabled={!switchEnabled}
        >
          <SelectPrimitive.Trigger asChild aria-label={t('shell.activeOrg')}>
            <button
              type="button"
              className="border-border-default border-l-accent-default hover:bg-surface-raised focus-visible:ring-accent-default flex w-full items-center gap-2 border-b border-l-2 px-2 py-3 text-left transition-colors focus-visible:ring-2 focus-visible:outline-none disabled:cursor-not-allowed md:px-4"
            >
              <Brandmark />
              {/* TASK-5987: below md the rail collapses to icons only — the
                  wordmark + context line are hidden but stay in the DOM; the
                  combobox keeps its "Active org" label. */}
              <span className="min-w-0 flex-1 flex-col max-md:hidden md:flex">
                <span className="font-['Baloo_2',sans-serif] text-[1rem] leading-tight font-extrabold tracking-[-0.03em]">
                  <span className="text-brand-foreground">Happy</span>
                  <span className="text-text-primary">Ranch</span>
                </span>
                {/* Context line — "Day N · <team>" (BUG-08). Day-N is the real
                    org_age_days from the dashboard summary; the team is the active
                    org slug. On a brand-new org (org_age_days 0/undefined) the day
                    token degrades away, leaving the bare slug — never "Day 0". */}
                <span className="text-text-muted truncate text-[0.7rem] leading-tight">
                  {activeSlug ? (
                    orgAgeDays && orgAgeDays > 0 ? (
                      <>
                        <span className="text-text-secondary">
                          {t('shell.dayCount', { count: orgAgeDays })}
                        </span>{' '}
                        · <span>{activeSlug}</span>
                      </>
                    ) : (
                      <span>{activeSlug}</span>
                    )
                  ) : (
                    t('shell.noOrg')
                  )}
                </span>
              </span>
              <ChevronDown size={14} aria-hidden="true" className="text-text-secondary hidden shrink-0 md:block" />
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
                <SelectItem value={ADD_ORG_VALUE}>{t('shell.addOrgOption')}</SelectItem>
              </>
            )}
          </SelectContent>
        </SelectPrimitive.Root>
      </section>

      <nav
        aria-label={t('shell.nav.primaryItems')}
        className="relative flex min-h-0 flex-1 scroll-py-1 flex-col gap-0.5 overflow-y-auto px-3 pt-3 pb-1"
      >
        <SidebarNavItem {...sidebarLink('dashboard', true)} icon={HomeIcon}>
          {t('shell.nav.home')}
        </SidebarNavItem>
        <SidebarNavItem
          to={routes.inboxForOrg(activeSlug ?? '')}
          enabled={!!activeSlug}
          icon={MessageSquare}
        >
          {t('shell.nav.threads')}
        </SidebarNavItem>
        <SidebarNavItem {...sidebarLink('tasks', true)} icon={ListChecks}>
          {t('shell.nav.tasks')}
        </SidebarNavItem>
        <SidebarNavItem {...sidebarLink('workflows', true)} icon={Workflow}>
          {t('shell.nav.workflows')}
        </SidebarNavItem>
        <SidebarNavItem {...sidebarLink('jobs', true)} icon={Terminal}>
          {t('shell.nav.jobs')}
        </SidebarNavItem>
        <SidebarNavItem {...sidebarLink('todos', true)} icon={ListChecks}>
          {t('shell.nav.todos')}
        </SidebarNavItem>
        <SidebarNavItem {...sidebarLink('agents', true)} icon={Users}>
          {t('shell.nav.agents')}
        </SidebarNavItem>
        {features.work_hours_ui && (
          <SidebarNavItem {...sidebarLink('work-hours', true)} icon={Clock}>
            {t('shell.nav.workHours')}
          </SidebarNavItem>
        )}
        <SidebarNavItem {...sidebarLink('skills', true)} icon={Puzzle}>
          {t('shell.nav.skills')}
        </SidebarNavItem>
        <SidebarNavItem {...sidebarLink('kb', true)} icon={BookOpen}>
          {t('shell.nav.knowledge')}
        </SidebarNavItem>
        <SidebarNavItem {...sidebarLink('artifacts', true)} icon={Package}>
          {t('shell.nav.artifacts')}
        </SidebarNavItem>
        <SidebarNavItem {...sidebarLink('audit', true)} icon={ScrollText}>
          {t('shell.nav.audit')}
        </SidebarNavItem>
        <SidebarNavItem {...sidebarLink('dreams', true)} icon={Sparkles}>
          {t('shell.nav.dreams')}
        </SidebarNavItem>
        <SidebarNavItem {...sidebarLink('usage', true)} icon={Wallet}>
          {t('shell.nav.usage')}
        </SidebarNavItem>
        <SidebarNavItem {...sidebarLink('health', true)} icon={Activity}>
          {t('shell.nav.health')}
        </SidebarNavItem>
      </nav>

      {/* Footer — Settings + account row (BUG-07). */}
      <div className="border-border-default mt-auto flex shrink-0 flex-col gap-1 border-t px-3 py-3">
        <SidebarNavItem {...sidebarLink('settings', true)} icon={Settings}>
          {t('shell.nav.settings')}
        </SidebarNavItem>
        {/* Account row — avatar + the human Founder's current name. */}
        <AccountRow />
      </div>

      {!isPrototype && <AddOrgDialog open={addOrgOpen} onOpenChange={setAddOrgOpen} />}
    </aside>
  );
}

/** The Founder's ready current name via the shared org-scoped identity query;
 * the canonical `founder` ID when unnamed, loading, unavailable or unbound. */
function AccountRow(): JSX.Element {
  return useHasIdentityProvider() ? <BoundAccountRow /> : <AccountRowView name="founder" />;
}
function BoundAccountRow(): JSX.Element {
  return <AccountRowView name={useFounderName()} />;
}
function AccountRowView({ name }: { name: string }): JSX.Element {
  const { t } = useTranslation();
  // Keyboard reachable as account context after the footer-pinned Settings item.
  return (
    <div
      tabIndex={0}
      aria-label={t('shell.account.label', { name, role: t('shell.account.founder') })}
      className="focus-visible:ring-accent-default flex items-center gap-2.5 rounded-sm px-2 py-1.5 focus-visible:ring-2 focus-visible:outline-none"
    >
      <Avatar initials={identityInitials(name)} tone="founder" size="md" />
      <span className="min-w-0 flex-col leading-tight max-md:hidden md:flex">
        <span className="text-text-primary truncate text-sm">{name}</span>
        <span className="text-text-muted truncate text-xs">{t('shell.account.founder')}</span>
      </span>
    </div>
  );
}

function Brandmark(): JSX.Element {
  return (
    <svg
      viewBox="0 0 100 100"
      width="30"
      height="30"
      aria-hidden="true"
      className="text-brand-foreground shrink-0"
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

export function SidebarNavItem({
  to,
  enabled,
  children,
  icon: Icon,
  tooltip,
}: {
  to: string;
  enabled: boolean;
  children: React.ReactNode;
  icon: LucideIcon;
  tooltip?: string;
}): JSX.Element {
  if (!enabled) {
    const span = (
      <span
        className="text-text-muted text-nav flex cursor-not-allowed items-center justify-center gap-2.5 rounded-sm px-2 py-1.5 font-medium md:justify-start md:gap-2.75 md:px-2.5 md:py-2"
        aria-disabled="true"
      >
        <Icon size={16} aria-hidden="true" className="shrink-0" />
        <span className="sr-only md:not-sr-only">{children}</span>
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
        // THR-376 PR2: Pasture `[data-dir="a"] .nav-item.active` — accent-soft
        // fill, accent-text ink, weight 600, no left bar; rows 13.5px/500.
        // THR-376 PR12: `.nav-item` padding 8px 10px and gap 11px from md up.
        // Below md the 56px icon rail keeps the base py-1.5 px-2 gap-2.5 rows.
        `flex items-center gap-2.5 rounded-sm px-2 py-1.5 md:gap-2.75 md:px-2.5 md:py-2 text-nav transition-colors focus-visible:ring-2 focus-visible:outline-none focus-visible:ring-accent-default justify-center md:justify-start ${
          isActive
            ? 'bg-accent-soft text-accent-text font-semibold'
            : 'text-text-secondary font-medium hover:bg-surface-raised hover:text-text-primary'
        }`
      }
    >
      <Icon size={16} aria-hidden="true" className="shrink-0" />
      {/* TASK-5987: sr-only below md keeps the accessible name when the rail
          collapses to icons; md+ restores the visible label. */}
      <span className="sr-only md:not-sr-only">{children}</span>
    </NavLink>
  );
}
