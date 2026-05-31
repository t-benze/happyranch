import { screen, waitFor } from '@testing-library/react';
import { http, HttpResponse, delay } from 'msw';
import { describe, expect, test } from 'vitest';
import { AppRoutes } from '@/routes';
import { renderWithProviders } from '@/test/render';
import { server } from '@/test/server';
import type { DashboardSummaryResponse } from '@/lib/api/types';

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
  );
}

function handler(summary: DashboardSummaryResponse) {
  return http.get(`/api/v1/orgs/${SLUG}/dashboard/summary`, () =>
    HttpResponse.json(summary),
  );
}

describe('DashboardPage', () => {
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
