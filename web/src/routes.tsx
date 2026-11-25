import { Fragment, type ReactNode } from 'react';
import {
  Navigate,
  Outlet,
  Route,
  Routes,
  useLocation,
  useMatch,
  useParams,
  useSearchParams,
} from 'react-router-dom';
import { AppBar } from '@/design-system/layouts/AppShell/AppBar';
import { ErrorBoundary, type ErrorBoundaryCopy } from '@/design-system/layouts/AppShell/ErrorBoundary';
import { Sidebar } from '@/design-system/layouts/AppShell/Sidebar';
import { TopbarSlotsProvider } from '@/design-system/layouts/AppShell/TopbarSlots';
import { useTranslation } from '@/hooks/i18n';
import { useOrgsList } from '@/hooks/orgs';
import { useNativeRecovery } from '@/hooks/nativeRecovery';
import { NativeNotice } from '@/shared/connection/NativeNotice';
import { NativeRecoveryFence } from '@/shared/connection/NativeRecoveryPanel';
import { OrgProvider } from '@/lib/orgSlug';
import { AgentsPage } from '@/features/agents/AgentsPage';
import { TeamEscalationPolicyPage } from '@/features/agents/TeamEscalationPolicyPage';
import { SequentialWorkflowsPage } from '@/features/workflows/SequentialWorkflowsPage';
import { JobsPage } from '@/features/jobs/JobsPage';
import { JobDetailPage } from '@/features/jobs/JobDetailPage';
import { AuditPage } from '@/features/audit/AuditPage';
import { SkillsPage } from '@/features/skills/SkillsPage';
import { SkillValidationPage } from '@/features/skills/SkillValidationPage';
import { SkillDetailPage } from '@/features/skills/SkillDetailPage';
import { CustomSkillsPage } from '@/features/skills/CustomSkillsPage';
import { CustomSkillCreatePage } from '@/features/skills/CustomSkillCreatePage';
import { CustomSkillDetailPage } from '@/features/skills/CustomSkillDetailPage';
import { DashboardPage } from '@/features/dashboard/DashboardPage';
import { KbPage } from '@/features/kb/KbPage';
import { TasksPage } from '@/features/tasks/TasksPage';
import { TaskDetailPage } from '@/features/tasks/TaskDetailPage';
import { UsagePage } from '@/features/usage/UsagePage';
import { HealthPage } from '@/features/health/HealthPage';
import { OnboardingPage } from '@/features/onboarding/OnboardingPage';
import { DreamsPage } from '@/features/dreams/DreamsPage';
import { OverviewPage as WorkHoursOverviewPage } from '@/features/work-hours-config/OverviewPage';
import { WakesView as WorkHoursWakesView } from '@/features/work-hours-config/WakesView';
import { AgentDetailPage as WorkHoursAgentDetailPage } from '@/features/work-hours-config/AgentDetailPage';
import { RecoverySettingsPage, SettingsPage } from '@/features/settings/SettingsPage';
import { SettingsDialogBody } from '@/features/settings/SettingsDialogBody';
import { ConnectionDialogBody } from '@/features/connection/ConnectionDialogBody';
import {
  DesktopDialogsProvider,
  DesktopSettingsRoute,
  MAIN_CONTENT_ID,
} from '@/features/desktop-dialogs/DesktopDialogs';
import { isDesktopShell } from '@/features/desktop-dialogs/desktopShell';
import { DesktopHeader } from '@/features/desktop-dialogs/DesktopHeader';
import { OwnerGateProvider } from '@/shared/owners';
import { ThreadsPage } from '@/features/threads/ThreadsPage';
import { TodosPage } from '@/features/todos/TodosPage';
import { PROTOTYPES_DISABLED, prototypeRoutes } from '@/prototypes';
import { features } from '@/config/features';

function RootRedirect(): JSX.Element {
  const orgsQuery = useOrgsList();
  const recovery = useNativeRecovery();
  const { t } = useTranslation();
  // A native recovery state (desktop: no target, starting, incompatible, ...)
  // is shown by the AppShell; it is not "no orgs", so never route to onboarding.
  if (recovery) return <></>;
  // THR-387 S5-W1 F3: only a successful (possibly empty) list routes; a
  // pending or failed read is never "no orgs", so an existing install is
  // never sent to onboarding on failure. A transient native failure
  // (busy/throttled) is refetched automatically once it clears.
  if (orgsQuery.isLoading) {
    return <div className="text-text-secondary p-8">{t('shell.loading')}</div>;
  }
  if (orgsQuery.data === undefined) {
    if (!orgsQuery.isError) return <div className="text-text-secondary p-8">{t('shell.loading')}</div>;
    return (
      <div className="text-text-secondary flex flex-col gap-3 p-8" data-root-orgs-error="">
        <div className="text-text-primary text-base font-semibold">{t('shell.error.title')}</div>
        <div className="text-xs">{t('shell.error.body')}</div>
      </div>
    );
  }
  const first = orgsQuery.data.orgs[0]?.slug;
  if (!first) {
    return <Navigate to="/onboarding" replace />;
  }
  return <Navigate to={`/orgs/${first}/dashboard`} replace />;
}

/**
 * Localized boundary for the routed content. The class boundary keeps its
 * captured error across a locale switch; only the surrounding app-owned copy
 * is re-supplied here.
 */
function AppShellErrorBoundary({
  resetKey,
  children,
}: {
  resetKey: string;
  children: ReactNode;
}): JSX.Element {
  const { t } = useTranslation();
  const copy: ErrorBoundaryCopy = {
    title: t('shell.error.title'),
    body: t('shell.error.body'),
    retry: t('shell.error.retry'),
  };
  return (
    <ErrorBoundary resetKey={resetKey} copy={copy}>
      {children}
    </ErrorBoundary>
  );
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
  // Settings fences only its target-backed sections itself, so local
  // Settings -> Preferences stays usable during a recovery state. The
  // org-less /settings/* is the recovery-only Settings for startup on `/`.
  const orgSettingsRoute = useMatch('/orgs/:slug/settings/*') !== null;
  const recoverySettingsRoute = useMatch('/settings/*') !== null;
  const settingsRoute = orgSettingsRoute || recoverySettingsRoute;
  return (
    <div className="flex h-full flex-col">
      {/* THR-387 seq146: desktop only (native v2 bridge). The whole window is
          the web view; this header replaces the native header bar. Browser
          mode renders nothing here. */}
      <DesktopHeader />
    <div className="flex min-h-0 flex-1 flex-row">
      <Sidebar />
      {/* THR-376 r2 PR-S1: pages portal their status chip / primary action
          into the AppBar through TopbarStatus / TopbarActions. */}
      <TopbarSlotsProvider>
        <div className="flex min-w-0 flex-1 flex-col">
          <AppBar />
          {/* THR-387 S5-W1: non-blocking busy/throttled/not-sent/unknown notice. */}
          <NativeNotice />
          <main id={MAIN_CONTENT_ID} tabIndex={-1} className="flex-1 overflow-hidden focus:outline-none">
            {/* THR-387 S1b: a native recovery state replaces the visible page
                but keeps it mounted (hidden) so drafts and page state survive. */}
            <NativeRecoveryFence bypass={settingsRoute}>
              <AppShellErrorBoundary resetKey={location.pathname}>
                <Outlet />
              </AppShellErrorBoundary>
            </NativeRecoveryFence>
          </main>
        </div>
      </TopbarSlotsProvider>
    </div>
    </div>
  );
}

/**
 * THR-387 S6-W2a: remount a Main editor page when its route params (org,
 * record id) change, so a draft never carries into another org or record.
 * The owner gate asks for Discard consent before such a change.
 */
function KeyedByParams({ children }: { children: ReactNode }): JSX.Element {
  const params = useParams();
  return <Fragment key={JSON.stringify(params)}>{children}</Fragment>;
}

export function AppRoutes(): JSX.Element {
  // THR-387 S6-W1: in the desktop app, Main Settings routes open the one
  // Settings dialog at that page; browser mode keeps the full-page routes.
  const desktop = isDesktopShell();
  // THR-387 S6-W2a: one owner registry + contextual owner gate per document,
  // in browser and desktop mode alike (it only acts on genuine unsaved edits
  // and outstanding saves).
  return (
    <OwnerGateProvider>
    <DesktopDialogsProvider
      renderSettingsBody={(slot) => <SettingsDialogBody {...slot} />}
      renderConnectionBody={(slot) => <ConnectionDialogBody {...slot} />}
    >
      <Routes>
        {/* Prototype routes mount OUTSIDE AppShell so the shell primitives inside
            `PrototypesLayout` run under `<PrototypeProvider>`'s QueryClient
            and OrgSlugContext — keeping mock-only behaviour fully isolated
            from the daemon-backed routes. */}
        {!PROTOTYPES_DISABLED && prototypeRoutes()}
        <Route element={<AppShell />}>
          <Route index element={<RootRedirect />} />
          <Route path="artifacts" element={<Navigate to="/" replace />} />
          {/* Onboarding is GLOBAL (not org-scoped): the Welcome/create/success
              shell drives the container-level /orgs list + create routes, so a
              slug is not yet chosen. Mounted in AppShell like the org-less index. */}
          <Route path="onboarding" element={<OnboardingPage />} />
          <Route path="/orgs/:slug" element={<OrgLayout />}>
            <Route index element={<NavigateToHome />} />
            <Route path="dashboard" element={<DashboardPage />} />
            <Route path="threads" element={<ThreadsPage />} />
            <Route path="threads/:thread_id" element={<ThreadsPage />} />
            <Route path="workflows" element={<SequentialWorkflowsPage view="definitions" />} />
            <Route path="workflows/setup" element={<SequentialWorkflowsPage view="setup" />} />
            <Route path="workflows/runs" element={<SequentialWorkflowsPage view="runs" />} />
            <Route path="workflows/definitions/:definitionId" element={<SequentialWorkflowsPage view="definition" />} />
            <Route path="workflows/runs/:runId" element={<SequentialWorkflowsPage view="run" />} />
            <Route path="tasks" element={<TasksPage />} />
            <Route path="tasks/:task_id" element={<TaskDetailPage />} />
            <Route path="todos" element={<TodosPage />} />
            <Route path="todos/:scheduleId" element={<TodosPage />} />
            <Route path="kb" element={<KbPage />} />
            <Route path="kb/:entrySlug/*" element={<KbPage />} />

            <Route path="audit" element={<AuditPage />} />
            <Route path="skills" element={<SkillsPage />} />
            {/* Static `new`/`validation` rank above the dynamic `:skillId` in
                react-router v6, but keep them declared first for readability. */}
            <Route path="skills/validation" element={<SkillValidationPage />} />
            <Route path="skills/custom" element={<CustomSkillsPage />} />
            <Route path="skills/custom/new" element={<KeyedByParams><CustomSkillCreatePage /></KeyedByParams>} />
            <Route path="skills/custom/:skillId" element={<KeyedByParams><CustomSkillDetailPage /></KeyedByParams>} />
            <Route path="skills/:skillId" element={<SkillDetailPage />} />
            <Route path="agents" element={<AgentsPage />} />
            <Route path="agents/:agent_name" element={<AgentsPage />} />
            <Route path="agents/:agent_name/team-escalation-policy" element={<TeamEscalationPolicyPage />} />
            <Route path="jobs" element={<JobsPage />} />
            <Route path="jobs/:job_id" element={<JobDetailPage />} />
            <Route path="health" element={<HealthPage />} />
            <Route path="usage" element={<UsagePage />} />
            {/* THR-061 seq79: Spend renamed to Usage. Keep the old /spend
                bookmark (and the dashboard deep-link) working via redirect. */}
            <Route path="spend" element={<SpendRedirect />} />
            <Route path="dreams" element={<DreamsPage />} />
            {/* THR-035: the standalone Schedule surface folded into Work Hours.
                Old bookmarks redirect to the Wakes view; the wake list now lives
                as the `?view=wakes` tab of the Work Hours surface.
                THR-305: with `work_hours_ui` off none of these routes exist, so
                the direct and legacy URLs fall through to the not-found route. */}
            {features.work_hours_ui && (
              <>
                <Route path="schedule" element={<ScheduleRedirect />} />
                <Route path="work-hours" element={<WorkHoursSurface />} />
                <Route path="work-hours/:agent" element={<WorkHoursAgentDetailPage />} />
              </>
            )}
            <Route path="artifacts" element={<Navigate to="../threads" replace />} />
            <Route path="settings/*" element={desktop ? <DesktopSettingsRoute /> : <SettingsPage />} />
          </Route>
          {/* THR-387 S1b: recovery-only org-less Settings (Preferences reachable
              during a startup recovery state with no known org). Outside
              recovery it renders NotFound, as this URL did before. */}
          <Route
            path="settings/*"
            element={desktop ? <DesktopSettingsRoute /> : <RecoverySettingsPage notFound={<NotFound />} />}
          />
          <Route path="*" element={<NotFound />} />
        </Route>
      </Routes>
    </DesktopDialogsProvider>
    </OwnerGateProvider>
  );
}

function NavigateToHome(): JSX.Element {
  const { slug } = useParams<{ slug: string }>();
  return <Navigate to={`/orgs/${slug}/dashboard`} replace />;
}

/**
 * Work Hours surface (THR-035): one route hosting two in-page tabs. The `Wakes`
 * tab (the wake-execution list folded in from the retired Schedule surface) is
 * selected by `?view=wakes`; everything else shows the config Overview. Using a
 * query param rather than a `work-hours/wakes` sub-route avoids any ranking
 * dependency against the sibling `work-hours/:agent` detail route.
 */
function WorkHoursSurface(): JSX.Element {
  const [searchParams] = useSearchParams();
  return searchParams.get('view') === 'wakes' ? (
    <WorkHoursWakesView />
  ) : (
    <WorkHoursOverviewPage />
  );
}

/** Redirect the retired /schedule surface to the Work Hours Wakes view. */
function ScheduleRedirect(): JSX.Element {
  const { slug } = useParams<{ slug: string }>();
  return <Navigate to={`/orgs/${slug}/work-hours?view=wakes`} replace />;
}

/** THR-061 seq79: /spend was renamed to /usage. Redirect the old path so
 *  existing bookmarks and the dashboard "This week's burn" deep-link resolve. */
function SpendRedirect(): JSX.Element {
  const { slug } = useParams<{ slug: string }>();
  return <Navigate to={`/orgs/${slug}/usage`} replace />;
}

function NotFound(): JSX.Element {
  const { t, render } = useTranslation();
  return (
    <div className="text-text-secondary p-8">
      {render('shell.notFound.body', {
        link: (
          <a href="/" className="text-accent-default hover:underline">
            {t('shell.notFound.goHome')}
          </a>
        ),
      })}
    </div>
  );
}
