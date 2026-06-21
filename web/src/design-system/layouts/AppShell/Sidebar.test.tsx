/**
 * IA-1, IA-2, IA-10 tests for the Direction-A design overhaul Phase 1b.
 *
 * - IA-1: Sidebar renders with Primary and Operate groups, theme toggle,
 *   org switcher, and Settings; TopBar is retired.
 * - IA-2: Default landing route resolves to Home/Dashboard.
 * - IA-10: Nav grouping (Primary / Operate) rendered correctly.
 */
import { screen, waitFor } from '@testing-library/react';
import { http, HttpResponse } from 'msw';
import { describe, expect, test } from 'vitest';
import { AppRoutes } from '@/routes';
import { renderWithProviders } from '@/test/render';
import { server } from '@/test/server';

const SLUG = 'test-org';

/**
 * Seed the MSW handlers that the AppShell + Sidebar query on mount.
 * The Sidebar fetches orgs list, agents, threads, etc. — we answer them all.
 */
function seedSidebarShell(): void {
  sessionStorage.setItem('happyranch.token', 'tok');
  server.use(
    http.get('/api/v1/orgs', () =>
      HttpResponse.json({ orgs: [{ slug: SLUG, root: '/x' }] }),
    ),
    http.get(`/api/v1/orgs/${SLUG}/agents`, () =>
      HttpResponse.json({ agents: [] }),
    ),
    http.get(`/api/v1/orgs/${SLUG}/threads`, () =>
      HttpResponse.json({ threads: [] }),
    ),
    http.get(`/api/v1/orgs/${SLUG}/threads/events`, () =>
      HttpResponse.text('', { headers: { 'content-type': 'text/event-stream' } }),
    ),
    http.get(`/api/v1/orgs/${SLUG}/dashboard/summary`, () =>
      HttpResponse.json({
        heartbeat: [],
        narrative_counts: {
          completed_today: 0,
          failed_today: 0,
          escalated_open: 0,
          kb_added_today: 0,
          agents_active_now: 0,
          spend_today_usd: 0,
        },
        escalations: [],
        active_by_team: [],
        recent_activity: [],
        updates_this_week: [],
        org_pulse: [],
        org_age_days: 0,
        server_now: '2026-06-17T12:00:00Z',
      }),
    ),
    http.get(`/api/v1/orgs/${SLUG}/tokens`, () =>
      HttpResponse.json({ rollup: [] }),
    ),
    http.get('/api/v1/health', () =>
      HttpResponse.json({ status: 'ok', active_runtime: '/Users/x/happyranch' }),
    ),
    http.get('/api/v1/orgs/:slug/dreams', () =>
      HttpResponse.json({ dreams: [] }),
    ),
  );
}

/** A dashboard/summary payload with overridable narrative_counts + org_age_days. */
function dashboardSummary(
  over: { org_age_days?: number; counts?: Partial<Record<string, number>> } = {},
) {
  return {
    heartbeat: [],
    narrative_counts: {
      completed_today: 0,
      failed_today: 0,
      escalated_open: 0,
      kb_added_today: 0,
      agents_active_now: 0,
      spend_today_usd: 0,
      ...(over.counts ?? {}),
    },
    escalations: [],
    active_by_team: [],
    recent_activity: [],
    updates_this_week: [],
    org_pulse: [],
    org_age_days: over.org_age_days ?? 0,
    server_now: '2026-06-17T12:00:00Z',
  };
}

describe('THR-030 BUG-08: sidebar Day/team context line', () => {
  test('renders "Day N · <org>" when org_age_days > 0', async () => {
    seedSidebarShell();
    server.use(
      http.get(`/api/v1/orgs/${SLUG}/dashboard/summary`, () =>
        HttpResponse.json(dashboardSummary({ org_age_days: 15 })),
      ),
    );
    renderWithProviders(<AppRoutes />, { route: `/orgs/${SLUG}/dashboard` });

    await waitFor(() => {
      expect(screen.getByText(`Day 15 · ${SLUG}`)).toBeInTheDocument();
    });
  });

  test('degrades to bare org slug (no garish "Day 0") on a brand-new org', async () => {
    seedSidebarShell(); // fixture already reports org_age_days: 0
    renderWithProviders(<AppRoutes />, { route: `/orgs/${SLUG}/dashboard` });

    await waitFor(() => {
      // The context line still names the org…
      expect(screen.getByText(SLUG)).toBeInTheDocument();
    });
    // …but never shows "Day 0".
    expect(screen.queryByText(/Day 0/)).toBeNull();
  });
});

describe('THR-030 BUG-03: nav count badges (wired from narrative_counts)', () => {
  test('positive counts render badges (Agents ← agents_active_now, Audit ← escalated_open)', async () => {
    seedSidebarShell();
    server.use(
      http.get(`/api/v1/orgs/${SLUG}/dashboard/summary`, () =>
        HttpResponse.json(
          dashboardSummary({ counts: { agents_active_now: 5, escalated_open: 3 } }),
        ),
      ),
    );
    renderWithProviders(<AppRoutes />, { route: `/orgs/${SLUG}/dashboard` });

    await waitFor(() => {
      expect(screen.getByRole('link', { name: /Agents/ })).toHaveTextContent('5');
    });
    expect(screen.getByRole('link', { name: /Audit/ })).toHaveTextContent('3');
  });

  test('zero counts render NO badge (no "0" noise, no NaN)', async () => {
    seedSidebarShell(); // fixture reports all narrative_counts: 0
    renderWithProviders(<AppRoutes />, { route: `/orgs/${SLUG}/dashboard` });

    await waitFor(() => {
      // Exact accessible name = "Agents"/"Audit" proves no trailing badge digit.
      expect(screen.getByRole('link', { name: 'Agents' })).toBeInTheDocument();
    });
    expect(screen.getByRole('link', { name: 'Audit' })).toBeInTheDocument();
    expect(screen.queryByText('NaN')).toBeNull();
  });
});

describe('IA-1: Sidebar (left rail replaces TopBar)', () => {
  test('renders Primary group nav items', async () => {
    seedSidebarShell();
    renderWithProviders(<AppRoutes />, { route: `/orgs/${SLUG}/dashboard` });

    await waitFor(() => {
      // Primary group label
      expect(screen.getByText('Primary')).toBeInTheDocument();
      // Primary nav items (Home matches both the nav link and the app-bar
      // page title, so target the nav link specifically).
      expect(screen.getByRole('link', { name: 'Home' })).toBeInTheDocument();
      expect(screen.getByText('Threads')).toBeInTheDocument();
      expect(screen.getByText('Tasks')).toBeInTheDocument();
      expect(screen.getByText('Agents')).toBeInTheDocument();
      expect(screen.getByText('Knowledge')).toBeInTheDocument();
      expect(screen.getByText('Artifacts')).toBeInTheDocument();
    });
  });

  test('renders Operate group nav items (IA-10)', async () => {
    seedSidebarShell();
    renderWithProviders(<AppRoutes />, { route: `/orgs/${SLUG}/dashboard` });

    await waitFor(() => {
      // Operate group label
      expect(screen.getByText('Operate')).toBeInTheDocument();
      // Operate nav items
      expect(screen.getByText('Spend')).toBeInTheDocument();
      expect(screen.getByText('Dreams')).toBeInTheDocument();
      expect(screen.getByText('Schedule')).toBeInTheDocument();
      expect(screen.getByText('Audit')).toBeInTheDocument();
    });
  });

  test('renders org switcher in footer', async () => {
    seedSidebarShell();
    renderWithProviders(<AppRoutes />, { route: `/orgs/${SLUG}/dashboard` });

    await waitFor(() => {
      expect(screen.getByLabelText(/Active org/i)).toBeInTheDocument();
    });
  });

  test('renders theme toggle in the top app bar (IA-1 Guardrail P5, THR-030 BUG-06)', async () => {
    seedSidebarShell();
    renderWithProviders(<AppRoutes />, { route: `/orgs/${SLUG}/dashboard` });

    await waitFor(() => {
      // Theme toggle exists (has aria-label with "theme") — now in the app bar.
      const themeBtn = screen.getByLabelText(/theme/i);
      expect(themeBtn).toBeInTheDocument();
      // It lives inside the top app bar banner, not the sidebar.
      expect(themeBtn.closest('[role="banner"]')).not.toBeNull();
    });
  });

  test('renders Settings labeled row in footer (THR-030 BUG-02)', async () => {
    seedSidebarShell();
    renderWithProviders(<AppRoutes />, { route: `/orgs/${SLUG}/dashboard` });

    await waitFor(() => {
      // Settings is now a labeled row (aria-label "Settings" + visible label).
      const settings = screen.getByLabelText('Settings');
      expect(settings).toBeInTheDocument();
      expect(settings).toHaveTextContent('Settings');
      expect(settings.closest('aside')).not.toBeNull();
    });
  });

  test('renders account identity row in footer (THR-030 BUG-07)', async () => {
    seedSidebarShell();
    renderWithProviders(<AppRoutes />, { route: `/orgs/${SLUG}/dashboard` });

    await waitFor(() => {
      expect(screen.getByText('You')).toBeInTheDocument();
      expect(screen.getByText('Founder')).toBeInTheDocument();
    });
  });

  test('legacy 9-tab TopBar is retired (only the minimal app bar remains)', async () => {
    seedSidebarShell();
    renderWithProviders(<AppRoutes />, { route: `/orgs/${SLUG}/dashboard` });

    await waitFor(() => {
      expect(screen.getByLabelText(/Active org/i)).toBeInTheDocument();
    });
    // The retired TopBar rendered "KB" / "Jobs" tabs as top-nav links — gone.
    expect(screen.queryByRole('link', { name: 'KB' })).toBeNull();
    expect(screen.queryByRole('link', { name: 'Jobs' })).toBeNull();
    // The new persistent app bar (THR-030 BUG-05) is the only banner.
    expect(screen.getAllByRole('banner')).toHaveLength(1);
  });

  test('Sidebar uses <aside> with navigation role', async () => {
    seedSidebarShell();
    renderWithProviders(<AppRoutes />, { route: `/orgs/${SLUG}/dashboard` });

    await waitFor(() => {
      const nav = screen.getByRole('navigation', { name: /Primary navigation/i });
      expect(nav.tagName).toBe('ASIDE');
    });
  });
});

describe('IA-2: Default landing = Home', () => {
  test('RootRedirect navigates to /dashboard (not /threads)', async () => {
    seedSidebarShell();
    sessionStorage.setItem('happyranch.token', 'tok');
    server.use(
      http.get('/api/v1/orgs', () =>
        HttpResponse.json({ orgs: [{ slug: SLUG, root: '/x' }] }),
      ),
      http.get(`/api/v1/orgs/${SLUG}/dashboard/summary`, () =>
        HttpResponse.json({
          heartbeat: [],
          narrative_counts: {
            completed_today: 0,
            failed_today: 0,
            escalated_open: 0,
            kb_added_today: 0,
            agents_active_now: 0,
            spend_today_usd: 0,
          },
          escalations: [],
          active_by_team: [],
          recent_activity: [],
          updates_this_week: [],
          org_pulse: [],
          org_age_days: 0,
          server_now: '2026-06-17T12:00:00Z',
        }),
      ),
      http.get(`/api/v1/orgs/${SLUG}/tokens`, () =>
        HttpResponse.json({ rollup: [] }),
      ),
      http.get('/api/v1/health', () =>
        HttpResponse.json({ status: 'ok', active_runtime: '/Users/x/happyranch' }),
      ),
    );

    // Navigate to root — should redirect to /orgs/test-org/dashboard
    renderWithProviders(<AppRoutes />, { route: '/' });

    // The dashboard first-run empty state should appear
    await waitFor(() => {
      expect(screen.getByText(/Start your first brief/i)).toBeInTheDocument();
    });
  });

  test('NavigateToHome redirects /orgs/:slug to /dashboard', async () => {
    seedSidebarShell();
    sessionStorage.setItem('happyranch.token', 'tok');

    // Navigate to /orgs/test-org (index) — should redirect to /dashboard
    renderWithProviders(<AppRoutes />, { route: `/orgs/${SLUG}` });

    await waitFor(() => {
      expect(screen.getByText(/Start your first brief/i)).toBeInTheDocument();
    });
    // Verify we are NOT on the threads page
    expect(screen.queryByRole('heading', { name: /Inbox/i })).toBeNull();
  });
});

describe('IA-10: Nav grouping (Primary / Operate)', () => {
  test('Primary group label appears before Operate group label', async () => {
    seedSidebarShell();
    renderWithProviders(<AppRoutes />, { route: `/orgs/${SLUG}/dashboard` });

    await waitFor(() => {
      const primaryEl = screen.getByText('Primary');
      const operateEl = screen.getByText('Operate');
      expect(primaryEl).toBeInTheDocument();
      expect(operateEl).toBeInTheDocument();
      // Primary appears before Operate in DOM order
      expect(
        primaryEl.compareDocumentPosition(operateEl) & Node.DOCUMENT_POSITION_FOLLOWING,
      ).toBeTruthy();
    });
  });

  test('Operate nav items link to placeholder pages', async () => {
    seedSidebarShell();
    renderWithProviders(<AppRoutes />, { route: `/orgs/${SLUG}/dashboard` });

    await waitFor(() => {
      const spendLink = screen.getByText('Spend');
      const dreamsLink = screen.getByText('Dreams');
      const scheduleLink = screen.getByText('Schedule');
      expect(spendLink.closest('a')).toHaveAttribute('href', `/orgs/${SLUG}/spend`);
      expect(dreamsLink.closest('a')).toHaveAttribute('href', `/orgs/${SLUG}/dreams`);
      expect(scheduleLink.closest('a')).toHaveAttribute('href', `/orgs/${SLUG}/schedule`);
    });
  });
});

describe('Operate surfaces', () => {
  test('renders Spend surface', async () => {
    seedSidebarShell();
    // Seed token endpoints so SpendPage doesn't error
    server.use(
      http.get(`/api/v1/orgs/${SLUG}/tokens`, ({ request }) => {
        const url = new URL(request.url);
        const groupBy = url.searchParams.get('group_by');
        return HttpResponse.json({ rollup: groupBy === 'model' ? [] : [] });
      }),
    );
    renderWithProviders(<AppRoutes />, { route: `/orgs/${SLUG}/spend` });
    await waitFor(() => {
      expect(screen.getByText(/Token usage and cache savings/i)).toBeInTheDocument();
    });
  });

  test('renders Dreams surface', async () => {
    seedSidebarShell();
    renderWithProviders(<AppRoutes />, { route: `/orgs/${SLUG}/dreams` });
    await waitFor(() => {
      const dreamsElements = screen.getAllByText('Dreams');
      expect(dreamsElements.length).toBeGreaterThanOrEqual(2); // sidebar nav + page header
      expect(screen.getByText(/Nightly agent reflections and knowledge proposals/i)).toBeInTheDocument();
    });
  });

  test('renders Schedule surface', async () => {
    seedSidebarShell();
    // Seed work-hours endpoint with empty list
    server.use(
      http.get(`/api/v1/orgs/${SLUG}/work-hours`, () =>
        HttpResponse.json({ work_hours: [] }),
      ),
    );
    renderWithProviders(<AppRoutes />, { route: `/orgs/${SLUG}/schedule` });
    await waitFor(() => {
      // 'Schedule' appears in both sidebar nav and page header
      const scheduleElements = screen.getAllByText('Schedule');
      expect(scheduleElements.length).toBeGreaterThanOrEqual(2);
      expect(screen.getByText(/No scheduled wakes/i)).toBeInTheDocument();
    });
  });
});

describe('/jobs is still reachable (not retired in Phase 1b)', () => {
  test('navigating to /jobs renders the jobs page', async () => {
    seedSidebarShell();
    server.use(
      http.get(`/api/v1/orgs/${SLUG}/jobs`, () =>
        HttpResponse.json({ jobs: [], total: 0 }),
      ),
    );
    renderWithProviders(<AppRoutes />, { route: `/orgs/${SLUG}/jobs` });
    await waitFor(() => {
      // JobsPage renders contextual guidance (PRD §4.13 — no standalone index)
      expect(screen.getByText(/Jobs are reachable contextually/)).toBeInTheDocument();
    });
  });
});
