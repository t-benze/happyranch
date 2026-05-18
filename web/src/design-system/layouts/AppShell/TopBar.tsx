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
import { useOrgsList } from '@/hooks/orgs';
import { useThreadRoutes } from '@/hooks/threads';
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
  const routes = useThreadRoutes();
  const threadsHref = activeSlug ? routes.inboxForOrg(activeSlug) : '#';
  const switchEnabled = !orgsQuery.isLoading && (orgsQuery.data?.orgs.length ?? 0) > 0;
  // The four placeholder tabs (Tasks/KB/Audit/Agents) live only on the
  // production routes — the prototype sandbox is threads-only. Disable
  // them inside `/__prototypes/*` so a click can't escape the sandbox.
  const placeholderTab = (path: string) => ({
    to: activeSlug && !isPrototype ? `/orgs/${activeSlug}/${path}` : '#',
    enabled: !!activeSlug && !isPrototype,
    tooltip: isPrototype ? 'Not in sandbox' : undefined,
  });

  return (
    <header className="border-border bg-bg-subtle flex h-12 shrink-0 items-center gap-4 border-b px-4">
      <div className="text-fg font-semibold">Grassland</div>
      <Select
        value={activeSlug ?? undefined}
        onValueChange={(target) => {
          if (!target || target === activeSlug) return;
          navigate(routes.inboxForOrg(target));
        }}
        disabled={!switchEnabled}
      >
        <SelectTrigger aria-label="Active org" className="w-[180px]">
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
      <nav className="flex items-center gap-1 text-sm">
        <NavTab to={threadsHref} enabled={!!activeSlug && threadsHref !== '#'}>
          Threads
        </NavTab>
        <NavTab {...placeholderTab('tasks')}>Tasks</NavTab>
        <NavTab {...placeholderTab('kb')}>KB</NavTab>
        <NavTab {...placeholderTab('audit')}>Audit</NavTab>
        <NavTab {...placeholderTab('agents')}>Agents</NavTab>
      </nav>
    </header>
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
