import { useQuery } from '@tanstack/react-query';
import {
  Navigate,
  Outlet,
  Route,
  Routes,
  useParams,
} from 'react-router-dom';
import { TopBar } from '@/components/TopBar';
import { orgs as orgsApi } from '@/lib/api';
import { OrgProvider } from '@/lib/orgSlug';
import { ThreadsPage } from '@/features/threads/ThreadsPage';

function RootRedirect(): JSX.Element {
  const orgsQuery = useQuery({ queryKey: ['orgs'], queryFn: orgsApi.listOrgs });
  if (orgsQuery.isLoading) {
    return <div className="p-6 text-fg-muted">Loading…</div>;
  }
  const first = orgsQuery.data?.orgs[0]?.slug;
  if (!first) {
    return (
      <div className="p-6 text-fg-muted">
        No orgs loaded. Run <code className="text-fg">opc orgs init &lt;slug&gt;</code> from the CLI.
      </div>
    );
  }
  return <Navigate to={`/orgs/${first}/threads`} replace />;
}

function OrgLayout(): JSX.Element {
  return (
    <OrgProvider>
      <Outlet />
    </OrgProvider>
  );
}

function AppShell(): JSX.Element {
  return (
    <div className="flex h-full flex-col">
      <TopBar />
      <main className="flex-1 overflow-hidden">
        <Outlet />
      </main>
    </div>
  );
}

export function AppRoutes(): JSX.Element {
  return (
    <Routes>
      <Route element={<AppShell />}>
        <Route index element={<RootRedirect />} />
        <Route path="/orgs/:slug" element={<OrgLayout />}>
          <Route index element={<NavigateToThreads />} />
          <Route path="threads" element={<ThreadsPage />} />
          <Route path="threads/:thread_id" element={<ThreadsPage />} />
        </Route>
        <Route path="*" element={<NotFound />} />
      </Route>
    </Routes>
  );
}

function NavigateToThreads(): JSX.Element {
  const { slug } = useParams<{ slug: string }>();
  return <Navigate to={`/orgs/${slug}/threads`} replace />;
}

function NotFound(): JSX.Element {
  return (
    <div className="p-6 text-fg-muted">
      Not found. <a href="/" className="text-accent hover:underline">Go home</a>.
    </div>
  );
}
