import { useQuery } from '@tanstack/react-query';
import { NavLink, useNavigate, useParams } from 'react-router-dom';
import { orgs as orgsApi } from '@/lib/api';

export function TopBar(): JSX.Element {
  const { slug } = useParams<{ slug: string }>();
  const navigate = useNavigate();
  const orgsQuery = useQuery({
    queryKey: ['orgs'],
    queryFn: () => orgsApi.listOrgs(),
  });

  return (
    <header className="flex h-12 shrink-0 items-center gap-4 border-b border-border bg-bg-subtle px-4">
      <div className="font-semibold text-fg">OPC</div>
      <select
        value={slug ?? ''}
        onChange={(e) => navigate(`/orgs/${e.target.value}/threads`)}
        className="rounded border border-border bg-bg-raised px-2 py-1 text-sm text-fg"
        aria-label="Active org"
        disabled={orgsQuery.isLoading || (orgsQuery.data?.orgs.length ?? 0) === 0}
      >
        {!slug && <option value="">Select org…</option>}
        {orgsQuery.data?.orgs.map((o) => (
          <option key={o.slug} value={o.slug}>
            {o.slug}
          </option>
        ))}
      </select>
      <nav className="flex items-center gap-1 text-sm">
        <NavTab to={slug ? `/orgs/${slug}/threads` : '#'} enabled={!!slug}>
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
