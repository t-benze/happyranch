import { NavLink, useNavigate, useParams } from 'react-router-dom';
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
  const orgsQuery = useOrgsList();
  const routes = useThreadRoutes();
  const threadsHref = activeSlug ? routes.inboxForOrg(activeSlug) : '#';
  const switchEnabled = !orgsQuery.isLoading && (orgsQuery.data?.orgs.length ?? 0) > 0;

  return (
    <header className="flex h-12 shrink-0 items-center gap-4 border-b border-border bg-bg-subtle px-4">
      <div className="font-semibold text-fg">OPC</div>
      <select
        value={activeSlug ?? ''}
        onChange={(e) => {
          const target = e.target.value;
          if (!target || target === activeSlug) return;
          navigate(routes.inboxForOrg(target));
        }}
        className="rounded border border-border bg-bg-raised px-2 py-1 text-sm text-fg"
        aria-label="Active org"
        disabled={!switchEnabled}
      >
        {!activeSlug && <option value="">Select org…</option>}
        {orgsQuery.data?.orgs.map((o) => (
          <option key={o.slug} value={o.slug}>
            {o.slug}
          </option>
        ))}
      </select>
      <nav className="flex items-center gap-1 text-sm">
        <NavTab to={threadsHref} enabled={!!activeSlug && threadsHref !== '#'}>
          Threads
        </NavTab>
        <NavTab to="#" enabled={false} title="Coming soon">
          Tasks
        </NavTab>
        <NavTab to="#" enabled={false} title="Coming soon">
          KB
        </NavTab>
        <NavTab to="#" enabled={false} title="Coming soon">
          Audit
        </NavTab>
        <NavTab to="#" enabled={false} title="Coming soon">
          Agents
        </NavTab>
      </nav>
    </header>
  );
}

function NavTab({
  to,
  enabled,
  children,
  title,
}: {
  to: string;
  enabled: boolean;
  children: React.ReactNode;
  title?: string;
}): JSX.Element {
  if (!enabled) {
    return (
      <span
        className="cursor-not-allowed rounded px-2 py-1 text-fg-subtle"
        title={title}
        aria-disabled="true"
      >
        {children}
      </span>
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
