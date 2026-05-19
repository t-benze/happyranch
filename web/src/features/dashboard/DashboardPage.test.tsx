import { screen, waitFor, within } from '@testing-library/react';
import { http, HttpResponse } from 'msw';
import { describe, expect, test } from 'vitest';
import { AppRoutes } from '@/routes';
import { renderWithProviders } from '@/test/render';
import { server } from '@/test/server';

const SLUG = 'hk-macau-tourism';

function task(overrides: Record<string, unknown>) {
  return {
    task_id: 'TASK-0001',
    team: 'content',
    brief: 'placeholder',
    status: 'in_progress',
    block_kind: null,
    parent_task_id: null,
    revisit_of_task_id: null,
    created_at: '2026-05-18T10:00:00Z',
    updated_at: '2026-05-18T10:00:00Z',
    closed_at: null,
    cancelled_at: null,
    session_timeout_seconds: null,
    ...overrides,
  };
}

const ESCALATED = task({
  task_id: 'TASK-ESC-1',
  team: 'cx',
  brief: 'Refund $280 awaiting founder',
  status: 'blocked',
  block_kind: 'escalated',
});

const DELEGATED = task({
  task_id: 'TASK-BLK-1',
  team: 'product',
  brief: 'Waiting on child worker',
  status: 'blocked',
  block_kind: 'delegated',
});

const ACTIVE_CONTENT = task({
  task_id: 'TASK-ACT-1',
  team: 'content',
  brief: 'Draft Hong Kong visa guide v2',
  status: 'in_progress',
});

const ACTIVE_OPS = task({
  task_id: 'TASK-ACT-2',
  team: 'ops',
  brief: 'Vet partner hotel candidates',
  status: 'in_progress',
});

const COMPLETED = task({
  task_id: 'TASK-DONE-1',
  team: 'content',
  brief: 'Already shipped',
  status: 'completed',
});

function mountAt(route: string) {
  sessionStorage.setItem('grassland.token', 'tok');
  server.use(
    http.get('/api/v1/orgs', () =>
      HttpResponse.json({ orgs: [{ slug: SLUG, root: '/x' }] }),
    ),
    http.get('/api/v1/health', () =>
      HttpResponse.json({ status: 'ok', active_runtime: '/Users/x/grassland' }),
    ),
    http.get(`/api/v1/orgs/${SLUG}/tasks`, () =>
      HttpResponse.json({
        tasks: [ESCALATED, DELEGATED, ACTIVE_CONTENT, ACTIVE_OPS, COMPLETED],
      }),
    ),
  );
  return renderWithProviders(<AppRoutes />, { route });
}

describe('DashboardPage', () => {
  test('renders all four card sections', async () => {
    mountAt(`/orgs/${SLUG}/dashboard`);
    await waitFor(() => {
      expect(screen.getByLabelText(/system health/i)).toBeInTheDocument();
      expect(screen.getByLabelText(/pending your action/i)).toBeInTheDocument();
      expect(screen.getByLabelText(/active tasks by team/i)).toBeInTheDocument();
      expect(screen.getByLabelText(/blocked tasks/i)).toBeInTheDocument();
    });
  });

  test('system health card shows daemon-ok + active runtime', async () => {
    mountAt(`/orgs/${SLUG}/dashboard`);
    const card = await screen.findByLabelText(/system health/i);
    await waitFor(() => {
      expect(within(card).getByText(/daemon: ok/i)).toBeInTheDocument();
    });
    expect(within(card).getByText(/grassland/i)).toBeInTheDocument();
  });

  test('pending your action lists only escalated-blocked tasks', async () => {
    mountAt(`/orgs/${SLUG}/dashboard`);
    const card = await screen.findByLabelText(/pending your action/i);
    await waitFor(() => {
      expect(within(card).getByText(/refund \$280/i)).toBeInTheDocument();
    });
    expect(within(card).queryByText(/waiting on child worker/i)).toBeNull();
    expect(within(card).queryByText(/draft hong kong/i)).toBeNull();
  });

  test('active tasks card groups by team', async () => {
    mountAt(`/orgs/${SLUG}/dashboard`);
    const card = await screen.findByLabelText(/active tasks by team/i);
    await waitFor(() => {
      expect(within(card).getByText(/draft hong kong/i)).toBeInTheDocument();
    });
    expect(within(card).getByText(/vet partner hotel/i)).toBeInTheDocument();
    // Team headings are rendered at least once (TaskCard also shows `team` in
    // muted text inside the row, so use getAllByText).
    expect(within(card).getAllByText(/^content$/i).length).toBeGreaterThan(0);
    expect(within(card).getAllByText(/^ops$/i).length).toBeGreaterThan(0);
    // Completed task does not leak in.
    expect(within(card).queryByText(/already shipped/i)).toBeNull();
  });

  test('blocked tasks card excludes escalations and completions', async () => {
    mountAt(`/orgs/${SLUG}/dashboard`);
    const card = await screen.findByLabelText(/blocked tasks/i);
    await waitFor(() => {
      expect(within(card).getByText(/waiting on child worker/i)).toBeInTheDocument();
    });
    expect(within(card).queryByText(/refund \$280/i)).toBeNull();
    expect(within(card).queryByText(/already shipped/i)).toBeNull();
  });

  test('empty buckets render their respective empty states', async () => {
    sessionStorage.setItem('grassland.token', 'tok');
    server.use(
      http.get('/api/v1/orgs', () =>
        HttpResponse.json({ orgs: [{ slug: SLUG, root: '/x' }] }),
      ),
      http.get('/api/v1/health', () =>
        HttpResponse.json({ status: 'ok', active_runtime: '/Users/x/grassland' }),
      ),
      http.get(`/api/v1/orgs/${SLUG}/tasks`, () =>
        HttpResponse.json({ tasks: [] }),
      ),
    );
    renderWithProviders(<AppRoutes />, { route: `/orgs/${SLUG}/dashboard` });

    const pending = await screen.findByLabelText(/pending your action/i);
    await waitFor(() => {
      expect(within(pending).getByText(/all clear/i)).toBeInTheDocument();
    });
    expect(
      within(screen.getByLabelText(/active tasks by team/i)).getByText(/no active tasks/i),
    ).toBeInTheDocument();
    expect(
      within(screen.getByLabelText(/blocked tasks/i)).getByText(/no blocked tasks/i),
    ).toBeInTheDocument();
  });
});
