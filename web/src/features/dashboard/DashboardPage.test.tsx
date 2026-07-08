import { screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { http, HttpResponse, delay } from 'msw';
import { describe, expect, test, vi, beforeEach } from 'vitest';
import { AppRoutes } from '@/routes';
import { renderWithProviders } from '@/test/render';
import { server } from '@/test/server';
import type { DashboardSummaryResponse } from '@/lib/api/types';

// Partial-mock the tokens hooks: stub the today-total and week-total hooks so
// the "Tokens today" tile and "This week's burn" card render deterministic
// figures, while leaving useTopThreadTokens REAL so the self-contained
// TopTokenThreadsPanel still rides its MSW-seeded /tokens fetch (same pattern
// as TopTokenThreadsPanel.test.tsx, scoped to the two new hooks).
vi.mock('@/hooks/tokens', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/hooks/tokens')>();
  return { ...actual, useTokensToday: vi.fn(), useTokensWeek: vi.fn() };
});
import { useTokensToday, useTokensWeek, formatTokens } from '@/hooks/tokens';
const mockTokensToday = vi.mocked(useTokensToday);
const mockTokensWeek = vi.mocked(useTokensWeek);

const SLUG = 'hk-macau-tourism';
const ROUTE = `/orgs/${SLUG}/dashboard`;

function emptySummary(): DashboardSummaryResponse {
  return {
    heartbeat: Array.from({ length: 24 }, (_, h) => ({
      hour: h,
      steps: 0,
      failed: 0,
      tier: 'ok',
    })),
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
    server_now: '2026-05-30T12:00:00Z',
  };
}

/**
 * Seed the surrounding routes that AppShell + TopBar query on mount.
 * MSW is configured with `onUnhandledRequest: 'error'`, so every call the
 * app makes must be answered — even ones not under test.
 */
function seedShell(): void {
  sessionStorage.setItem('happyranch.token', 'tok');
  server.use(
    http.get('/api/v1/orgs', () =>
      HttpResponse.json({ orgs: [{ slug: SLUG, root: '/x' }] }),
    ),
    http.get('/api/v1/health', () =>
      HttpResponse.json({ status: 'ok', active_runtime: '/Users/x/happyranch' }),
    ),
    // The self-contained TopTokenThreadsPanel fetches its own thread rollup
    // on every established-org render; answer it (empty) so msw's
    // onUnhandledRequest:'error' guard stays satisfied.
    http.get(`/api/v1/orgs/${SLUG}/tokens`, () =>
      HttpResponse.json({ rollup: [] }),
    ),
  );
}

function handler(summary: DashboardSummaryResponse) {
  return http.get(`/api/v1/orgs/${SLUG}/dashboard/summary`, () =>
    HttpResponse.json(summary),
  );
}

describe('DashboardPage', () => {
  beforeEach(() => {
    // Default: a quiet today. Individual tests override as needed.
    mockTokensToday.mockReturnValue({
      data: 0,
      isLoading: false,
      isError: false,
      error: null,
    });
    // Default: a quiet week's burn. Individual tests override as needed.
    mockTokensWeek.mockReturnValue({
      data: 0,
      isLoading: false,
      isError: false,
      error: null,
    });
  });

  test('renders loading state initially', async () => {
    seedShell();
    server.use(
      http.get(`/api/v1/orgs/${SLUG}/dashboard/summary`, async () => {
        await delay(200);
        return HttpResponse.json(emptySummary());
      }),
    );
    renderWithProviders(<AppRoutes />, { route: ROUTE });
    expect(await screen.findByText(/Loading dashboard/i)).toBeInTheDocument();
  });

  test('renders error state on 500', async () => {
    seedShell();
    server.use(
      http.get(`/api/v1/orgs/${SLUG}/dashboard/summary`, () =>
        new HttpResponse(null, { status: 500 }),
      ),
    );
    renderWithProviders(<AppRoutes />, { route: ROUTE });
    await waitFor(() => {
      expect(screen.getByText(/Failed to load dashboard/i)).toBeInTheDocument();
    });
  });

  test('renders first-run empty state when org_age_days is 0 and no activity', async () => {
    seedShell();
    server.use(handler(emptySummary()));
    renderWithProviders(<AppRoutes />, { route: ROUTE });
    await waitFor(() => {
      expect(screen.getByText(/Start your first brief/i)).toBeInTheDocument();
    });
  });

  test('renders All clear when established org has no escalations', async () => {
    const s = emptySummary();
    s.org_age_days = 14;
    s.narrative_counts.completed_today = 5;
    seedShell();
    server.use(handler(s));
    renderWithProviders(<AppRoutes />, { route: ROUTE });
    await waitFor(() => {
      expect(screen.getByText(/All clear/i)).toBeInTheDocument();
    });
    // The completed count surfaces in the narrative paragraph: "5 tasks completed".
    expect(screen.getByText(/tasks completed/i)).toBeInTheDocument();
  });

  test('renders escalation rows and pending count', async () => {
    const s = emptySummary();
    s.org_age_days = 14;
    s.narrative_counts.completed_today = 5;
    s.narrative_counts.escalated_open = 1;
    s.escalations = [
      {
        task_id: 'TASK-101',
        agent: 'qa_engineer',
        team: 'engineering',
        question: 'Photo licensing unclear',
        raised_at: '2026-05-30T11:00:00Z',
        age_seconds: 3600,
      },
    ];
    seedShell();
    server.use(handler(s));
    renderWithProviders(<AppRoutes />, { route: ROUTE });
    await waitFor(() => {
      expect(screen.getByText(/Waiting on you · 1/i)).toBeInTheDocument();
    });
    expect(screen.getByText(/Photo licensing unclear/i)).toBeInTheDocument();
  });

  test('lays out a main column (queue + activity feed) beside a right rail (secondary cards)', async () => {
    const s = emptySummary();
    s.org_age_days = 14;
    s.narrative_counts.completed_today = 5;
    seedShell();
    server.use(handler(s));
    renderWithProviders(<AppRoutes />, { route: ROUTE });

    const main = await screen.findByTestId('dashboard-main');
    const rail = screen.getByTestId('dashboard-rail');

    // Main column: Waiting-on-you queue on top + Recent-activity feed below.
    expect(within(main).getByText(/Waiting on you/i)).toBeInTheDocument();
    expect(within(main).getByText(/^Recent activity$/)).toBeInTheDocument();

    // Right rail: the secondary cards (Today heartbeat + Org pulse).
    expect(within(rail).getByText(/^Today$/)).toBeInTheDocument();
    expect(within(rail).getByText(/Org pulse/i)).toBeInTheDocument();

    // The escalation queue moved out of the old right column — it is no
    // longer in the rail.
    expect(within(rail).queryByText(/Waiting on you/i)).not.toBeInTheDocument();
  });

  test('section headers mix serif main-column card titles with small-eyebrow rail labels (THR-030 HOME-07)', async () => {
    const s = emptySummary();
    s.org_age_days = 14;
    s.narrative_counts.completed_today = 5;
    seedShell();
    server.use(handler(s));
    renderWithProviders(<AppRoutes />, { route: ROUTE });

    const main = await screen.findByTestId('dashboard-main');
    const rail = screen.getByTestId('dashboard-rail');

    // Main-column primary cards: serif (var(--font-display)) titles, NOT the
    // all-caps utilitarian label. Both must be softened off the eyebrow style.
    const waiting = within(main).getByText(/Waiting on you/i);
    expect(waiting).toHaveClass('font-display');
    expect(waiting).not.toHaveClass('uppercase');
    const recent = within(main).getByText(/^Recent activity$/);
    expect(recent).toHaveClass('font-display');
    expect(recent).not.toHaveClass('uppercase');

    // Right-rail secondary cards stay small uppercase eyebrows (no serif role).
    const today = within(rail).getByText(/^Today$/);
    expect(today).toHaveClass('uppercase');
    expect(today).not.toHaveClass('font-display');
  });

  test('greeting heading is a serif status summary derived from the waiting count', async () => {
    const s = emptySummary();
    s.org_age_days = 14;
    s.narrative_counts.completed_today = 5;
    s.narrative_counts.escalated_open = 2;
    s.escalations = [
      {
        task_id: 'TASK-201',
        agent: 'qa_engineer',
        team: 'engineering',
        question: 'Photo licensing unclear',
        raised_at: '2026-05-30T11:00:00Z',
        age_seconds: 3600,
      },
      {
        task_id: 'TASK-202',
        agent: 'dev_agent',
        team: 'engineering',
        question: 'Schema migration scope',
        raised_at: '2026-05-30T10:00:00Z',
        age_seconds: 7200,
      },
    ];
    seedShell();
    server.use(handler(s));
    renderWithProviders(<AppRoutes />, { route: ROUTE });

    const heading = await screen.findByRole('heading', { level: 1 });
    // Typography: Newsreader display serif role (var(--font-display)).
    expect(heading).toHaveClass('font-display');
    // Copy: derived from the real waiting count (2 escalations), not hardcoded.
    expect(heading).toHaveTextContent(/2 things need you/i);
  });

  test('greeting heading reads all-caught-up when nothing is waiting', async () => {
    const s = emptySummary();
    s.org_age_days = 14;
    s.narrative_counts.completed_today = 5;
    s.escalations = [];
    seedShell();
    server.use(handler(s));
    renderWithProviders(<AppRoutes />, { route: ROUTE });

    const heading = await screen.findByRole('heading', { level: 1 });
    expect(heading).toHaveClass('font-display');
    expect(heading).toHaveTextContent(/all caught up/i);
  });

  test('TODAY card shows an honest Tokens today tile and no Spend today dollars tile (THR-030 HOME-04)', async () => {
    const s = emptySummary();
    s.org_age_days = 14;
    s.narrative_counts.completed_today = 5;
    // A real today-scoped token total flows from the (mocked) useTokensToday.
    mockTokensToday.mockReturnValue({
      data: 26_500_000,
      isLoading: false,
      isError: false,
      error: null,
    });
    seedShell();
    server.use(handler(s));
    renderWithProviders(<AppRoutes />, { route: ROUTE });

    const rail = await screen.findByTestId('dashboard-rail');

    // The honest tokens tile: label + value rendered via the shared compact
    // formatter (26_500_000 -> '26.5M'), NOT a hand-rolled string.
    expect(within(rail).getByText('Tokens today')).toBeInTheDocument();
    expect(
      within(rail).getByText(formatTokens(26_500_000)),
    ).toBeInTheDocument();

    // The dishonest dollars counter-tile is gone from the TODAY grid.
    expect(within(rail).queryByText('Spend today')).not.toBeInTheDocument();
    expect(within(rail).queryByText(/\$0\.00/)).not.toBeInTheDocument();
  });

  test('Tokens today tile shows a neutral placeholder (not 0) while the token query is pending', async () => {
    const s = emptySummary();
    s.org_age_days = 14;
    s.narrative_counts.completed_today = 5;
    // Token query still in flight: data undefined. The tile must NOT paint a
    // fabricated 0 (THR-030 HOME-04 — value comes only from real data).
    mockTokensToday.mockReturnValue({
      data: undefined,
      isLoading: true,
      isError: false,
      error: null,
    });
    seedShell();
    server.use(handler(s));
    renderWithProviders(<AppRoutes />, { route: ROUTE });

    const rail = await screen.findByTestId('dashboard-rail');
    const tile = within(rail).getByText('Tokens today').parentElement as HTMLElement;
    expect(within(tile).getByText('—')).toBeInTheDocument();
    expect(within(tile).queryByText('0')).not.toBeInTheDocument();
  });

  test('Tokens today tile shows a neutral placeholder (not 0) when the token query errors', async () => {
    const s = emptySummary();
    s.org_age_days = 14;
    s.narrative_counts.completed_today = 5;
    // Token query failed: data undefined, isError true. The tile must show the
    // same neutral state — never a dishonest 0.
    mockTokensToday.mockReturnValue({
      data: undefined,
      isLoading: false,
      isError: true,
      error: new Error('boom'),
    });
    seedShell();
    server.use(handler(s));
    renderWithProviders(<AppRoutes />, { route: ROUTE });

    const rail = await screen.findByTestId('dashboard-rail');
    const tile = within(rail).getByText('Tokens today').parentElement as HTMLElement;
    expect(within(tile).getByText('—')).toBeInTheDocument();
    expect(within(tile).queryByText('0')).not.toBeInTheDocument();
  });

  test('Tokens today tile renders the real summed total via formatTokens on success', async () => {
    const s = emptySummary();
    s.org_age_days = 14;
    s.narrative_counts.completed_today = 5;
    // A real today-scoped total resolved by the (mocked) summing hook.
    mockTokensToday.mockReturnValue({
      data: 1_250_000,
      isLoading: false,
      isError: false,
      error: null,
    });
    seedShell();
    server.use(handler(s));
    renderWithProviders(<AppRoutes />, { route: ROUTE });

    const rail = await screen.findByTestId('dashboard-rail');
    const tile = within(rail).getByText('Tokens today').parentElement as HTMLElement;
    expect(within(tile).getByText(formatTokens(1_250_000))).toBeInTheDocument();
    // The success state replaces the neutral placeholder entirely.
    expect(within(tile).queryByText('—')).not.toBeInTheDocument();
  });

  test("This week's burn card renders a real 7d token figure with a chevron deep-link to Usage (THR-030 HOME-06)", async () => {
    const s = emptySummary();
    s.org_age_days = 14;
    // A real this-week token total resolved by the (mocked) week-summing hook.
    mockTokensWeek.mockReturnValue({
      data: 274_400_991,
      isLoading: false,
      isError: false,
      error: null,
    });
    seedShell();
    server.use(handler(s));
    renderWithProviders(<AppRoutes />, { route: ROUTE });

    const rail = await screen.findByTestId('dashboard-rail');
    const card = within(rail)
      .getByText("This week's burn")
      .closest('section') as HTMLElement;

    // Honest token figure via the shared compact formatter, scoped to the card.
    expect(within(card).getByText(formatTokens(274_400_991))).toBeInTheDocument();
    // Tokens are the unit (dollar burn is deferred — no '$' on this card).
    expect(within(card).getByText('Tokens')).toBeInTheDocument();
    expect(within(card).queryByText(/\$/)).not.toBeInTheDocument();

    // The chevron affordance deep-links into the Spend page (same-window number).
    const link = within(card).getByRole('link', { name: /view token spend/i });
    expect(link).toHaveAttribute('href', `/orgs/${SLUG}/spend`);
  });

  test("This week's burn card shows a neutral placeholder (not 0) while the token query is pending", async () => {
    const s = emptySummary();
    s.org_age_days = 14;
    // Week query still in flight: data undefined. The card must NOT paint a
    // fabricated 0 (honesty fence — value comes only from real data).
    mockTokensWeek.mockReturnValue({
      data: undefined,
      isLoading: true,
      isError: false,
      error: null,
    });
    seedShell();
    server.use(handler(s));
    renderWithProviders(<AppRoutes />, { route: ROUTE });

    const rail = await screen.findByTestId('dashboard-rail');
    const card = within(rail)
      .getByText("This week's burn")
      .closest('section') as HTMLElement;
    expect(within(card).getByText('—')).toBeInTheDocument();
    expect(within(card).queryByText('0')).not.toBeInTheDocument();
  });

  test("This week's burn card shows a neutral placeholder (not 0) when the token query errors", async () => {
    const s = emptySummary();
    s.org_age_days = 14;
    // Week query failed: data undefined, isError true. Same neutral state —
    // never a dishonest 0.
    mockTokensWeek.mockReturnValue({
      data: undefined,
      isLoading: false,
      isError: true,
      error: new Error('boom'),
    });
    seedShell();
    server.use(handler(s));
    renderWithProviders(<AppRoutes />, { route: ROUTE });

    const rail = await screen.findByTestId('dashboard-rail');
    const card = within(rail)
      .getByText("This week's burn")
      .closest('section') as HTMLElement;
    expect(within(card).getByText('—')).toBeInTheDocument();
    expect(within(card).queryByText('0')).not.toBeInTheDocument();
  });

  test('EscalationInboxRow approve submits blank rationale', async () => {
    // THR-046: dashboard inline approve allows blank rationale.
    const s = emptySummary();
    s.org_age_days = 14;
    s.narrative_counts.completed_today = 3;
    s.narrative_counts.escalated_open = 1;
    s.escalations = [
      {
        task_id: 'TASK-301',
        agent: 'qa_engineer',
        team: 'engineering',
        question: 'Photo licensing unclear',
        raised_at: '2026-05-30T11:00:00Z',
        age_seconds: 3600,
      },
    ];
    seedShell();

    let resolveBody: unknown = null;
    server.use(
      handler(s),
      http.post(
        `/api/v1/orgs/${SLUG}/tasks/TASK-301/resolve-escalation`,
        async ({ request }) => {
          resolveBody = await request.json();
          return HttpResponse.json({ ok: true, task_id: 'TASK-301', new_status: 'pending' });
        },
      ),
    );

    const user = userEvent.setup();
    renderWithProviders(<AppRoutes />, { route: ROUTE });

    // Wait for the escalation row to render, then click it to expand.
    await screen.findByText(/Photo licensing unclear/i);
    const row = screen.getByText(/Photo licensing unclear/i).closest(
      '.border-border-subtle',
    ) as HTMLElement;
    await user.click(row);

    // The expanded view shows an "Approve & resolve" button.
    // Click it without typing any rationale.
    await user.click(
      screen.getByRole('button', { name: /Approve & resolve/i }),
    );

    // The mutation fires asynchronously; wait for the captured body to be set.
    await waitFor(() => {
      expect(resolveBody).not.toBeNull();
    });

    expect(resolveBody).toEqual({ decision: 'approve', rationale: '' });
  });

  test('renders org pulse table when teams exist', async () => {
    const s = emptySummary();
    s.org_age_days = 14;
    s.narrative_counts.completed_today = 3;
    s.org_pulse = [
      {
        team: 'engineering',
        acceptance_pct: 87,
        trend_delta: -3,
        sparkline: [
          0.85, 0.86, 0.87, 0.88, 0.86, 0.85, 0.87, 0.88, 0.86, 0.87, 0.86,
          0.87,
        ],
        members: 4,
        lead: 'engineering_head',
      },
    ];
    seedShell();
    server.use(handler(s));
    renderWithProviders(<AppRoutes />, { route: ROUTE });
    await waitFor(() => {
      expect(screen.getByText(/engineering_head/)).toBeInTheDocument();
    });
    expect(screen.getByText(/87%/)).toBeInTheDocument();
  });
});
