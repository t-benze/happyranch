/**
 * IA-1, IA-2, IA-10 tests for the Direction-A design overhaul Phase 1b.
 *
 * - IA-1: Sidebar renders with Primary and Operate groups, theme toggle,
 *   org switcher, and Settings; TopBar is retired.
 * - IA-2: Default landing route resolves to Home/Dashboard.
 * - IA-10: Nav grouping (Primary / Operate) rendered correctly.
 */
import { screen, waitFor, within } from '@testing-library/react';
import { http, HttpResponse } from 'msw';
import { describe, expect, test } from 'vitest';
import { AppRoutes } from '@/routes';
import type { NarrativeCounts } from '@/lib/api/types';
import { renderWithProviders } from '@/test/render';
import { server } from '@/test/server';

const SLUG = 'test-org';

/**
 * Seed the MSW handlers that the AppShell + Sidebar query on mount.
 * The Sidebar fetches orgs list, agents, threads, etc. — we answer them all.
 *
 * `summary` overrides the dashboard-summary payload the Sidebar context header
 * (Day-N, BUG-08) and nav badges (BUG-03) read via the shared
 * `useDashboardSummary()` query. Defaults to a brand-new org (Day 0, all counts
 * zero) — the degraded / badge-less state.
 */
function seedSidebarShell(
  summary: { org_age_days?: number; narrative_counts?: Partial<NarrativeCounts> } = {},
): void {
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
          ...summary.narrative_counts,
        },
        escalations: [],
        active_by_team: [],
        recent_activity: [],
        updates_this_week: [],
        org_pulse: [],
        org_age_days: summary.org_age_days ?? 0,
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

describe('IA-1: Sidebar (left rail replaces TopBar)', () => {
  test('renders Primary group nav items', async () => {
    seedSidebarShell();
    renderWithProviders(<AppRoutes />, { route: `/orgs/${SLUG}/dashboard` });

    await waitFor(() => {
      // Scope to the sidebar — the AppBar now mirrors the page name (e.g.
      // "Home"), so unscoped getByText would match twice.
      const aside = within(screen.getByRole('navigation', { name: /Primary navigation/i }));
      // Primary group label
      expect(aside.getByText('Primary')).toBeInTheDocument();
      // Primary nav items
      expect(aside.getByText('Home')).toBeInTheDocument();
      expect(aside.getByText('Threads')).toBeInTheDocument();
      expect(aside.getByText('Tasks')).toBeInTheDocument();
      expect(aside.getByText('Agents')).toBeInTheDocument();
      expect(aside.getByText('Knowledge')).toBeInTheDocument();
      expect(aside.getByText('Artifacts')).toBeInTheDocument();
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

  test('renders org switcher as the top context header (BUG-01)', async () => {
    seedSidebarShell();
    renderWithProviders(<AppRoutes />, { route: `/orgs/${SLUG}/dashboard` });

    await waitFor(() => {
      // The context header is the org switcher (keeps the "Active org" label)
      // and is no longer a native footer combobox.
      expect(screen.getByLabelText(/Active org/i)).toBeInTheDocument();
      // Context line shows the team (active org slug) under the wordmark.
      const aside = within(screen.getByRole('navigation', { name: /Primary navigation/i }));
      expect(aside.getByText(SLUG)).toBeInTheDocument();
    });
  });

  test('renders theme toggle in the app bar (BUG-06)', async () => {
    seedSidebarShell();
    renderWithProviders(<AppRoutes />, { route: `/orgs/${SLUG}/dashboard` });

    await waitFor(() => {
      // Theme toggle exists (has aria-label with "theme") — relocated to AppBar.
      const themeBtn = screen.getByLabelText(/theme/i);
      expect(themeBtn).toBeInTheDocument();
    });
  });

  test('renders the global search affordance in the app bar (BUG-04/05)', async () => {
    seedSidebarShell();
    renderWithProviders(<AppRoutes />, { route: `/orgs/${SLUG}/dashboard` });

    await waitFor(() => {
      // "Ask or search" opens the Assistant Dock — now in the top app bar.
      expect(screen.getByLabelText('Ask or search')).toBeInTheDocument();
    });
  });

  test('renders Settings as a labeled row linking to the settings route (BUG-02)', async () => {
    seedSidebarShell();
    renderWithProviders(<AppRoutes />, { route: `/orgs/${SLUG}/dashboard` });

    await waitFor(() => {
      const settings = screen.getByRole('link', { name: 'Settings' });
      expect(settings).toHaveAttribute('href', `/orgs/${SLUG}/settings`);
    });
  });

  test('renders the account identity row (BUG-07)', async () => {
    seedSidebarShell();
    renderWithProviders(<AppRoutes />, { route: `/orgs/${SLUG}/dashboard` });

    await waitFor(() => {
      expect(screen.getByText('You')).toBeInTheDocument();
      expect(screen.getByText('Founder')).toBeInTheDocument();
    });
  });

  test('TopBar is retired — no tab-bar header role exists', async () => {
    seedSidebarShell();
    renderWithProviders(<AppRoutes />, { route: `/orgs/${SLUG}/dashboard` });

    await waitFor(() => {
      expect(screen.getByLabelText(/Active org/i)).toBeInTheDocument();
    });
    // The old TopBar rendered a <header role="banner"> — it should NOT exist
    expect(screen.queryByRole('banner')).toBeNull();
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

describe('BUG-08: real Day-N context line (wired to org_age_days)', () => {
  function aside() {
    return within(screen.getByRole('navigation', { name: /Primary navigation/i }));
  }

  test('renders "Day N · <team>" from org_age_days when positive', async () => {
    seedSidebarShell({ org_age_days: 15 });
    renderWithProviders(<AppRoutes />, { route: `/orgs/${SLUG}/dashboard` });

    await waitFor(() => {
      // Real day value, reference order "Day N · <team>". Scoped to the
      // sidebar so it doesn't collide with the page body.
      expect(aside().getByText(/Day\s*15/)).toBeInTheDocument();
      expect(aside().getByText(SLUG)).toBeInTheDocument();
    });
  });

  test('degrades to the bare org slug when org_age_days is 0 (new org)', async () => {
    seedSidebarShell({ org_age_days: 0 });
    renderWithProviders(<AppRoutes />, { route: `/orgs/${SLUG}/dashboard` });

    await waitFor(() => {
      expect(aside().getByText(SLUG)).toBeInTheDocument();
    });
    // Never "Day 0" or "Day —" — the day token is suppressed entirely.
    expect(aside().queryByText(/Day/)).toBeNull();
  });
});

describe('BUG-03: real nav badges (honest, no placeholder noise)', () => {
  test('Agents and Audit render badges from positive narrative_counts', async () => {
    seedSidebarShell({
      narrative_counts: { agents_active_now: 3, escalated_open: 2 },
    });
    renderWithProviders(<AppRoutes />, { route: `/orgs/${SLUG}/dashboard` });

    await waitFor(() => {
      const agents = screen.getByText('Agents').closest('a');
      expect(agents).not.toBeNull();
      expect(within(agents!).getByTestId('nav-count-badge')).toHaveTextContent('3');

      const audit = screen.getByText('Audit').closest('a');
      expect(audit).not.toBeNull();
      expect(within(audit!).getByTestId('nav-count-badge')).toHaveTextContent('2');
    });
  });

  test('zero / undefined count suppresses the badge (no "0" noise)', async () => {
    seedSidebarShell({
      narrative_counts: { agents_active_now: 0, escalated_open: 0 },
    });
    renderWithProviders(<AppRoutes />, { route: `/orgs/${SLUG}/dashboard` });

    await waitFor(() => {
      expect(screen.getByText('Agents').closest('a')).not.toBeNull();
    });
    expect(
      within(screen.getByText('Agents').closest('a')!).queryByTestId('nav-count-badge'),
    ).toBeNull();
    expect(
      within(screen.getByText('Audit').closest('a')!).queryByTestId('nav-count-badge'),
    ).toBeNull();
  });

  test('Threads / Tasks / Dreams render badge-less (no client-side count exists)', async () => {
    // Even with rich counts present, these rows have NO backing field in
    // narrative_counts, so they must stay badge-less (scope fence).
    seedSidebarShell({
      narrative_counts: { agents_active_now: 3, escalated_open: 2 },
    });
    renderWithProviders(<AppRoutes />, { route: `/orgs/${SLUG}/dashboard` });

    await waitFor(() => {
      expect(screen.getByText('Threads').closest('a')).not.toBeNull();
    });
    for (const label of ['Threads', 'Tasks', 'Dreams']) {
      const row = screen.getByText(label).closest('a');
      expect(row).not.toBeNull();
      expect(within(row!).queryByTestId('nav-count-badge')).toBeNull();
    }
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
