import {
  Navigate,
  Outlet,
  Route,
  Routes,
  useLocation,
  useParams,
} from 'react-router-dom';
import { ErrorBoundary } from '@/design-system/layouts/AppShell/ErrorBoundary';
import { TopBar } from '@/design-system/layouts/AppShell/TopBar';
import { useOrgsList } from '@/hooks/orgs';
import { OrgProvider } from '@/lib/orgSlug';
import { AgentsPage } from '@/features/agents/AgentsPage';
import { ArtifactsPage } from '@/features/artifacts/ArtifactsPage';
import { JobsPage } from '@/features/jobs/JobsPage';
import { CommandPaletteHost } from '@/host/CommandPaletteHost';
import { HelpDrawerHost } from '@/host/HelpDrawerHost';
import { ActivityTab } from '@/features/audit/ActivityTab';
import { AuditPage } from '@/features/audit/AuditPage';
import { EscalationsTab } from '@/features/audit/EscalationsTab';
import { TracesTab } from '@/features/audit/TracesTab';
import { DashboardPage } from '@/features/dashboard/DashboardPage';
import { KbPage } from '@/features/kb/KbPage';
import { TalksPage } from '@/features/talks/TalksPage';
import { TasksPage } from '@/features/tasks/TasksPage';
import { ThreadsPage } from '@/features/threads/ThreadsPage';
import { PROTOTYPES_DISABLED, prototypeRoutes } from '@/prototypes';
import { DESIGN_ROUTE_DISABLED, designRoutes } from '@/design-system/__design__';

function RootRedirect(): JSX.Element {
  const orgsQuery = useOrgsList();
  if (orgsQuery.isLoading) {
    return <div className="text-fg-muted p-6">Loading…</div>;
  }
  const first = orgsQuery.data?.orgs[0]?.slug;
  if (!first) {
    return (
      <div className="text-fg-muted p-6">
        No orgs loaded. Run <code className="text-fg">happyranch orgs init &lt;slug&gt;</code> from the CLI.
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
  const location = useLocation();
  return (
    <div className="flex h-full flex-col">
      <TopBar />
      <main className="flex-1 overflow-hidden">
        <ErrorBoundary resetKey={location.pathname}>
          <Outlet />
        </ErrorBoundary>
      </main>
      <CommandPaletteHost />
      <HelpDrawerHost />
    </div>
  );
}

export function AppRoutes(): JSX.Element {
  return (
    <Routes>
      {/* Prototype routes mount OUTSIDE AppShell so the TopBar + nav inside
          `PrototypesLayout` run under `<PrototypeProvider>`'s QueryClient
          and OrgSlugContext — keeping mock-only behaviour fully isolated
          from the daemon-backed routes. */}
      {!PROTOTYPES_DISABLED && prototypeRoutes()}
      {!DESIGN_ROUTE_DISABLED && designRoutes()}
      <Route element={<AppShell />}>
        <Route index element={<RootRedirect />} />
        <Route path="/orgs/:slug" element={<OrgLayout />}>
          <Route index element={<NavigateToThreads />} />
          <Route path="dashboard" element={<DashboardPage />} />
          <Route path="threads" element={<ThreadsPage />} />
          <Route path="threads/:thread_id" element={<ThreadsPage />} />
          <Route path="tasks" element={<TasksPage />} />
          <Route path="tasks/:task_id" element={<TasksPage />} />
          <Route path="kb" element={<KbPage />} />
          <Route path="kb/*" element={<KbPage />} />
          <Route path="talks" element={<TalksPage />} />
          <Route path="talks/:talk_id" element={<TalksPage />} />
          <Route path="audit" element={<AuditPage />}>
            <Route index element={<ActivityTab />} />
            <Route path="escalations" element={<EscalationsTab />} />
            <Route path="traces" element={<TracesTab />} />
            <Route path="traces/:task_id" element={<TracesTab />} />
          </Route>
          <Route path="agents" element={<AgentsPage />} />
          <Route path="agents/:agent_name" element={<AgentsPage />} />
          <Route path="jobs" element={<JobsPage />} />
          <Route path="jobs/:job_id" element={<JobsPage />} />
          <Route path="artifacts" element={<ArtifactsPage />} />
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
    <div className="text-fg-muted p-6">
      Not found. <a href="/" className="text-accent hover:underline">Go home</a>.
    </div>
  );
}
