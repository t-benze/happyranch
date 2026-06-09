import { useState } from 'react';
import { Moon, Plus, Rows3, Rows4, Sun } from 'lucide-react';
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
import { useDensity } from '@/hooks/density';
import { useKbRoutes } from '@/hooks/kb';
import { useOrgsList } from '@/hooks/orgs';
import { useTalksRoutes } from '@/hooks/talks';
import { useTasksRoutes } from '@/hooks/tasks';
import { useTheme } from '@/hooks/theme';
import { useThreadRoutes } from '@/hooks/threads';
import { useGlobalJump } from '@/hooks/global-jump';
import { useOrgSlugOptional } from '@/lib/orgSlug';

export function TopBar(): JSX.Element {
  // The TopBar mounts in two different shells: production AppShell (above
  // OrgProvider, so the slug is only in the URL params) and the prototype
  // sandbox layout (inside StaticOrgProvider, so the slug is in context but
  // not in the URL). Reading both and preferring whichever is present makes
  // the same component work in both trees.
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
  const talksRoutes = useTalksRoutes();
  const agentsRoutes = useAgentsRoutes();
  const threadsHref = activeSlug ? routes.inboxForOrg(activeSlug) : '#';
  const agentsHref = activeSlug && !isPrototype ? agentsRoutes.inboxForOrg(activeSlug) : '#';
  useGlobalJump('d', () => {
    if (activeSlug && !isPrototype) navigate(`/orgs/${activeSlug}/dashboard`);
  });
  useGlobalJump('i', () => {
    if (activeSlug && !isPrototype) navigate(routes.inboxForOrg(activeSlug));
  });
  useGlobalJump('t', () => {
    if (activeSlug && !isPrototype) navigate(tasksRoutes.inboxForOrg(activeSlug));
  });
  const kbRoutes = useKbRoutes();
  useGlobalJump('k', () => {
    if (activeSlug && !isPrototype) navigate(kbRoutes.inboxForOrg(activeSlug));
  });
  useGlobalJump('l', () => {
    if (activeSlug && !isPrototype) navigate(talksRoutes.inboxForOrg(activeSlug));
  });
  useGlobalJump('a', () => {
    if (activeSlug && !isPrototype) navigate(`/orgs/${activeSlug}/audit`);
  });
  useGlobalJump('g', () => {
    if (activeSlug && !isPrototype) navigate(agentsRoutes.inboxForOrg(activeSlug));
  });
  const switchEnabled = !orgsQuery.isLoading && (orgsQuery.data?.orgs.length ?? 0) > 0;
  // The Tasks/KB/Talks/Audit/Agents tabs live only on the production
  // routes — the prototype sandbox is threads-only. Disable them inside
  // `/__prototypes/*` so a click can't escape the sandbox.
  const placeholderTab = (path: string) => ({
    to: activeSlug && !isPrototype ? `/orgs/${activeSlug}/${path}` : '#',
    enabled: !!activeSlug && !isPrototype,
    tooltip: isPrototype ? 'Not in sandbox' : undefined,
  });

  return (
    <header
      role="banner"
      className="border-border bg-bg-subtle flex h-12 shrink-0 items-center gap-4 border-b px-4"
    >
      {/* Brand lockup: lasso mark + Baloo 2 wordmark */}
      <div className="flex shrink-0 items-center gap-1.5">
        <svg
          viewBox="0 0 100 100"
          width="22"
          height="22"
          aria-hidden="true"
          className="text-accent"
          style={{ color: '#4ade80' }}
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
        <span
          style={{
            fontFamily: "'Baloo 2', sans-serif",
            fontWeight: 800,
            fontSize: '1rem',
            letterSpacing: '-0.03em',
            lineHeight: 1,
          }}
        >
          <span style={{ color: '#4ade80' }}>Happy</span>
          <span className="text-fg">Ranch</span>
        </span>
      </div>
      <nav aria-label="Primary" className="flex items-center gap-1 text-sm">
        <NavTab {...placeholderTab('dashboard')}>Dashboard</NavTab>
        <NavTab to={threadsHref} enabled={!!activeSlug && threadsHref !== '#'}>
          Threads
        </NavTab>
        <NavTab {...placeholderTab('tasks')}>Tasks</NavTab>
        <NavTab {...placeholderTab('kb')}>KB</NavTab>
        <NavTab {...placeholderTab('talks')}>Talks</NavTab>
        <NavTab {...placeholderTab('audit')}>Audit</NavTab>
        <NavTab to={agentsHref} enabled={agentsHref !== '#'}>
          Agents
        </NavTab>
        <NavTab {...placeholderTab('jobs')}>Jobs</NavTab>
        <NavTab {...placeholderTab('assets')}>Assets</NavTab>
      </nav>
      <div className="ml-auto flex items-center gap-2">
        <Select
          value={activeSlug ?? undefined}
          onValueChange={(target) => {
            if (!target || target === activeSlug) return;
            // Stay on the same section; strip any detail suffix (e.g. task/thread id)
            // so we land on the section inbox, not a stale detail route.
            const sectionMatch = activeSlug
              ? location.pathname.match(
                  new RegExp(`^/orgs/${activeSlug}/([^/]+)`),
                )
              : null;
            const section = sectionMatch?.[1];
            navigate(section ? `/orgs/${target}/${section}` : routes.inboxForOrg(target));
          }}
          disabled={!switchEnabled}
        >
          <SelectTrigger aria-label="Active org" className="w-[160px]">
            <SelectValue placeholder="Select org…" />
          </SelectTrigger>
          <SelectContent>
            {orgsQuery.data?.orgs.map((o) => (
              <SelectItem key={o.slug} value={o.slug}>
                {o.slug}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
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
        <DensityToggle />
        <ThemeToggle />
      </div>
      {!isPrototype && <AddOrgDialog open={addOrgOpen} onOpenChange={setAddOrgOpen} />}
    </header>
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

function DensityToggle(): JSX.Element {
  const { density, setDensity } = useDensity();
  const isComfortable = density === 'comfortable';
  const label = isComfortable
    ? 'Switch to compact density'
    : 'Switch to comfortable density';
  return (
    <button
      type="button"
      aria-label={label}
      title={label}
      onClick={() => setDensity(isComfortable ? 'compact' : 'comfortable')}
      className="text-fg-muted hover:bg-bg-raised hover:text-fg focus-visible:ring-accent inline-flex h-7 w-7 items-center justify-center rounded transition-colors focus-visible:ring-2 focus-visible:outline-none"
    >
      {isComfortable ? (
        <Rows4 size={16} aria-hidden="true" />
      ) : (
        <Rows3 size={16} aria-hidden="true" />
      )}
    </button>
  );
}

export const meta = {
  name: "TopBar",
  layer: "layout",
  import: "@/design-system/layouts/AppShell/TopBar",
  variants: {},
  consumes: ["components.topbar", "layout.grid.app_shell"],
  example: "<TopBar />",
} as const;

function NavTab({
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
        className="text-fg-subtle cursor-not-allowed rounded px-2 py-1"
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
        `rounded px-2 py-1 ${
          isActive ? 'bg-bg-raised text-fg' : 'text-fg-muted hover:text-fg'
        }`
      }
    >
      {children}
    </NavLink>
  );
}
