import {
  Navigate,
  Outlet,
  Route,
  Routes,
  useLocation,
  useParams,
} from 'react-router-dom';
import { ErrorBoundary } from '@/design-system/layouts/AppShell/ErrorBoundary';
import { Sidebar } from '@/design-system/layouts/AppShell/Sidebar';
import { useOrgsList } from '@/hooks/orgs';
import { OrgProvider } from '@/lib/orgSlug';
import { AgentsPage } from '@/features/agents/AgentsPage';
import { ArtifactsPage } from '@/features/artifacts/ArtifactsPage';
import { JobsPage } from '@/features/jobs/JobsPage';
import { CommandPaletteHost } from '@/host/CommandPaletteHost';
import { HelpDrawerHost } from '@/host/HelpDrawerHost';
import { AuditPage } from '@/features/audit/AuditPage';
import { DashboardPage } from '@/features/dashboard/DashboardPage';
import { KbPage } from '@/features/kb/KbPage';
import { TasksPage } from '@/features/tasks/TasksPage';
import { SpendPage } from '@/features/spend/SpendPage';
import { SystemAssistantPage } from '@/features/system-assistant/SystemAssistantPage';
import { DreamsPage } from '@/features/dreams/DreamsPage';
import { SchedulePage } from '@/features/schedule/SchedulePage';
import { SettingsPage } from '@/features/settings/SettingsPage';
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
  return <Navigate to={`/orgs/${first}/dashboard`} replace />;
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
    <div className="flex h-full flex-row">
      <Sidebar />
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
          <Route index element={<NavigateToHome />} />
          <Route path="dashboard" element={<DashboardPage />} />
          <Route path="threads" element={<ThreadsPage />} />
          <Route path="threads/:thread_id" element={<ThreadsPage />} />
          <Route path="tasks" element={<TasksPage />} />
          <Route path="tasks/:task_id" element={<TasksPage />} />
          <Route path="kb" element={<KbPage />} />
          <Route path="kb/*" element={<KbPage />} />

          <Route path="audit" element={<AuditPage />} />
          <Route path="agents" element={<AgentsPage />} />
          <Route path="agents/:agent_name" element={<AgentsPage />} />
          <Route path="jobs" element={<JobsPage />} />
          <Route path="jobs/:job_id" element={<JobsPage />} />
          <Route path="spend" element={<SpendPage />} />
          <Route path="dreams" element={<DreamsPage />} />
          <Route path="schedule" element={<SchedulePage />} />
          <Route path="artifacts" element={<ArtifactsPage />} />
          <Route path="assistant" element={<SystemAssistantPage />} />
          <Route path="settings/*" element={<SettingsPage />} />
        </Route>
        <Route path="*" element={<NotFound />} />
      </Route>
    </Routes>
  );
}

function NavigateToHome(): JSX.Element {
  const { slug } = useParams<{ slug: string }>();
  return <Navigate to={`/orgs/${slug}/dashboard`} replace />;
}

function NotFound(): JSX.Element {
  return (
    <div className="text-fg-muted p-6">
      Not found. <a href="/" className="text-accent hover:underline">Go home</a>.
    </div>
  );
}
