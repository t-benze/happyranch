/**
 * Page-level wiring tests: TaskDetailPage renders the server-derived
 * Execution status card from the task-detail envelope (TASK-5522).
 *
 * These prove the card actually appears in the real page flow (not just the
 * standalone component), that it distinguishes heartbeat from agent-written
 * updates, and that a legacy envelope WITHOUT work_status preserves the
 * previous empty behavior (no card, no error).
 */
import { screen, waitFor } from '@testing-library/react';
import { http, HttpResponse } from 'msw';
import { describe, expect, test } from 'vitest';
import { AppRoutes } from '@/routes';
import { renderWithProviders } from '@/test/render';
import { server } from '@/test/server';
import type { TaskRecord, WorkStatusResponse } from '@/lib/api/types';

const SLUG = 'hk-macau-tourism';

const TASK: TaskRecord = {
  task_id: 'TASK-0091',
  team: 'content',
  brief: 'Draft Hong Kong visa guide v2',
  status: 'in_progress',
  block_kind: null,
  assigned_agent: 'content_writer',
  parent_task_id: null,
  revisit_of_task_id: null,
  created_at: '2026-08-23T10:00:00Z',
  updated_at: '2026-08-23T10:06:12Z',
  closed_at: null,
  cancelled_at: null,
  session_timeout_seconds: null,
};

function stubDetail(ws: WorkStatusResponse | null) {
  server.use(
    http.get('/api/v1/orgs', () =>
      HttpResponse.json({ orgs: [{ slug: SLUG, root: '/x' }] }),
    ),
    http.get(`/api/v1/orgs/${SLUG}/tasks`, () =>
      HttpResponse.json({ tasks: [TASK] }),
    ),
    http.get(`/api/v1/orgs/${SLUG}/tasks/${TASK.task_id}`, () =>
      HttpResponse.json({
        task: TASK,
        results: [],
        audit_log: [],
        revisit_chain: [],
        direct_revisits: [],
        predecessor_prior_status: null,
        ...(ws ? { work_status: ws } : {}),
      }),
    ),
    http.get(`/api/v1/orgs/${SLUG}/tasks/${TASK.task_id}/recall`, () =>
      HttpResponse.json({
        task_id: TASK.task_id,
        assigned_agent: 'content_writer',
        brief: TASK.brief,
        status: TASK.status,
        output_summary: null,
        children: [],
      }),
    ),
    http.get(`/api/v1/orgs/${SLUG}/tasks/${TASK.task_id}/events`, () =>
      HttpResponse.text('', { headers: { 'content-type': 'text/event-stream' } }),
    ),
  );
}

function ws(overrides: Partial<WorkStatusResponse>): WorkStatusResponse {
  return {
    applicable: true,
    state: 'stale_no_receipt',
    label: 'Stale-but-alive — no substantive update recorded',
    reason: null,
    session_start_ts: '2026-08-23T10:00:00Z',
    heartbeat: { timestamp: '2026-08-23T10:06:00Z', freshness: 'fresh' },
    latest_progress: null,
    ...overrides,
  };
}

describe('TaskDetailPage execution status card (TASK-5522)', () => {
  test('pending detail request keeps the loading shell free of fabricated status data', async () => {
    sessionStorage.setItem('happyranch.token', 'tok');
    stubDetail(ws({}));
    let detailRequested = false;
    server.use(
      http.get(`/api/v1/orgs/${SLUG}/tasks/${TASK.task_id}`, () => {
        detailRequested = true;
        return new Promise(() => {});
      }),
    );

    renderWithProviders(<AppRoutes />, {
      route: `/orgs/${SLUG}/tasks/${TASK.task_id}`,
    });

    await waitFor(() => expect(detailRequested).toBe(true));
    expect(
      screen.getByRole('heading', { name: TASK.task_id }),
    ).toBeInTheDocument();
    expect(screen.getByRole('heading', { name: 'Recall tree' })).toBeInTheDocument();
    expect(
      screen.queryByRole('complementary', {
        name: 'Task status and properties',
      }),
    ).toBeNull();
    expect(screen.queryByRole('region', { name: 'Execution status' })).toBeNull();
    expect(
      screen.queryByText('Stale-but-alive — no substantive update recorded'),
    ).toBeNull();
  });

  test('detail error preserves the route shell without stale or partial status data', async () => {
    sessionStorage.setItem('happyranch.token', 'tok');
    stubDetail(ws({}));
    let detailRequested = false;
    server.use(
      http.get(`/api/v1/orgs/${SLUG}/tasks/${TASK.task_id}`, () => {
        detailRequested = true;
        return HttpResponse.json({ detail: 'Task lookup failed' }, { status: 500 });
      }),
    );

    renderWithProviders(<AppRoutes />, {
      route: `/orgs/${SLUG}/tasks/${TASK.task_id}`,
    });

    await waitFor(() => expect(detailRequested).toBe(true));
    expect(
      screen.getByRole('heading', { name: TASK.task_id }),
    ).toBeInTheDocument();
    expect(screen.getByRole('heading', { name: 'Recall tree' })).toBeInTheDocument();
    expect(
      screen.queryByRole('complementary', {
        name: 'Task status and properties',
      }),
    ).toBeNull();
    expect(screen.queryByRole('region', { name: 'Execution status' })).toBeNull();
    expect(
      screen.queryByText('Stale-but-alive — no substantive update recorded'),
    ).toBeNull();
  });

  test('renders the card for a fresh-heartbeat/no-receipt task', async () => {
    sessionStorage.setItem('happyranch.token', 'tok');
    stubDetail(ws({}));
    renderWithProviders(<AppRoutes />, {
      route: `/orgs/${SLUG}/tasks/${TASK.task_id}`,
    });

    const card = await screen.findByRole('complementary', {
      name: 'Task status and properties',
    });
    expect(card.textContent).toContain(
      'Stale-but-alive — no substantive update recorded',
    );
    // Heartbeat liveness and the no-update line coexist but stay distinct.
    expect(card.textContent).toContain('(fresh)');
    expect(card.textContent).toContain('No substantive update recorded');
  });

  test('renders the agent-written message for recent progress', async () => {
    sessionStorage.setItem('happyranch.token', 'tok');
    stubDetail(
      ws({
        state: 'recent_progress',
        label: 'Recent update recorded',
        latest_progress: {
          timestamp: '2026-08-23T10:05:00Z',
          message: 'Phase 3 of 6: tests passing',
          agent: 'content_writer',
        },
      }),
    );
    renderWithProviders(<AppRoutes />, {
      route: `/orgs/${SLUG}/tasks/${TASK.task_id}`,
    });

    const card = await screen.findByRole('complementary', {
      name: 'Task status and properties',
    });
    expect(card.textContent).toContain('Recent update recorded');
    expect(card.textContent).toContain('Phase 3 of 6: tests passing');
  });

  test('legacy envelope without work_status renders no card and no error', async () => {
    sessionStorage.setItem('happyranch.token', 'tok');
    stubDetail(null);
    renderWithProviders(<AppRoutes />, {
      route: `/orgs/${SLUG}/tasks/${TASK.task_id}`,
    });

    // The page still loads its normal content…
    await screen.findByText('TASK-0091');
    // …and no execution-status card is invented.
    expect(
      screen.queryByRole('region', { name: 'Execution status' }),
    ).toBeNull();
    expect(
      await screen.findByRole('complementary', {
        name: 'Task status and properties',
      }),
    ).toBeInTheDocument();
  });
});
