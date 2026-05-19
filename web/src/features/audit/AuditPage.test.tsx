import { screen, waitFor } from '@testing-library/react';
import { http, HttpResponse } from 'msw';
import { describe, expect, test } from 'vitest';
import { AppRoutes } from '@/routes';
import { renderWithProviders } from '@/test/render';
import { server } from '@/test/server';

const SLUG = 'alpha';

function mountAt(route: string) {
  server.use(
    http.get('/api/v1/orgs', () =>
      HttpResponse.json({ orgs: [{ slug: SLUG, root: '/x' }] }),
    ),
  );
  return renderWithProviders(<AppRoutes />, { route });
}

function seedAudit() {
  server.use(
    http.get(`/api/v1/orgs/${SLUG}/audit`, ({ request }) => {
      const url = new URL(request.url);
      const agent = url.searchParams.get('agent');
      return HttpResponse.json({
        entries: [
          {
            id: 1,
            task_id: 'TASK-1',
            session_id: 'sess-1',
            agent: agent ?? 'content_writer',
            action: 'completion_report',
            payload: { status: 'completed' },
            created_at: '2026-05-19T11:00:00Z',
          },
        ],
      });
    }),
  );
}

describe('AuditPage', () => {
  test('renders activity feed by default and honors agent filter from URL', async () => {
    sessionStorage.setItem('grassland.token', 'tok');
    seedAudit();
    mountAt(`/orgs/${SLUG}/audit?agent=alice`);
    await waitFor(() =>
      expect(screen.getByText('alice')).toBeInTheDocument(),
    );
    // The audit row renders the action text under the toggle button; the
    // sidebar Type group also renders "completion_report" as a chip. The
    // row is identifiable by its accessible toggle label.
    const toggle = screen.getByRole('button', { name: /toggle row/i });
    expect(toggle).toHaveTextContent('completion_report');
    // The "agent" chip is set by the URL deep link; the active-filter banner
    // surfaces it back to the founder.
    expect(screen.getByText(/agent: alice/i)).toBeInTheDocument();
  });

  test('escalations sub-route mounts the escalations tab', async () => {
    sessionStorage.setItem('grassland.token', 'tok');
    seedAudit();
    mountAt(`/orgs/${SLUG}/audit/escalations`);
    await waitFor(() =>
      expect(
        screen.getByRole('tab', { name: 'Escalations' }),
      ).toHaveAttribute('aria-selected', 'true'),
    );
  });

  test('traces sub-route shows the empty picker prompt without a selected task', async () => {
    sessionStorage.setItem('grassland.token', 'tok');
    // Empty audit list → no recent tasks → "Pick a task" prompt.
    server.use(
      http.get(`/api/v1/orgs/${SLUG}/audit`, () =>
        HttpResponse.json({ entries: [] }),
      ),
    );
    mountAt(`/orgs/${SLUG}/audit/traces`);
    await waitFor(() =>
      expect(screen.getByText(/Pick a task/i)).toBeInTheDocument(),
    );
  });

  test('escalations forwards ?task_id= to the wire query', async () => {
    // Regression: previously the Escalations query dropped filters.task_id,
    // so the table showed org-wide escalations under a "task: X" banner.
    sessionStorage.setItem('grassland.token', 'tok');
    let lastTaskId: string | null = null;
    server.use(
      http.get(`/api/v1/orgs/${SLUG}/audit`, ({ request }) => {
        const url = new URL(request.url);
        lastTaskId = url.searchParams.get('task_id');
        return HttpResponse.json({ entries: [] });
      }),
    );
    mountAt(`/orgs/${SLUG}/audit/escalations?task_id=TASK-7`);
    await waitFor(() => expect(lastTaskId).toBe('TASK-7'));
  });

  test('escalations pairs a resolution authored by a different agent', async () => {
    // Regression: previously the wire query carried agent=alice, which
    // filtered out escalation_resolved rows authored by the founder/peer
    // manager — the table then showed the escalation as still open.
    sessionStorage.setItem('grassland.token', 'tok');
    server.use(
      http.get(`/api/v1/orgs/${SLUG}/audit`, () =>
        HttpResponse.json({
          entries: [
            {
              id: 1,
              task_id: 'TASK-99',
              session_id: null,
              agent: 'alice',
              action: 'escalation',
              payload: {},
              created_at: '2026-05-19T10:00:00Z',
            },
            {
              id: 2,
              task_id: 'TASK-99',
              session_id: null,
              agent: 'founder',
              action: 'escalation_resolved',
              payload: {},
              created_at: '2026-05-19T10:30:00Z',
            },
          ],
        }),
      ),
    );
    mountAt(`/orgs/${SLUG}/audit/escalations?agent=alice`);
    await waitFor(() =>
      expect(screen.getByText(/resolved/i)).toBeInTheDocument(),
    );
    expect(screen.queryByText(/^open$/i)).not.toBeInTheDocument();
  });

  test('traces honors ?task_id= deep link without a path segment', async () => {
    // Regression: clicking "View audit →" from a Task lands on /audit?task_id=X,
    // then switching to Traces via the SubTabBar should render the selected
    // task's trace — not the generic "Pick a task" prompt.
    sessionStorage.setItem('grassland.token', 'tok');
    server.use(
      http.get(`/api/v1/orgs/${SLUG}/audit`, () =>
        HttpResponse.json({ entries: [] }),
      ),
      http.get(`/api/v1/orgs/${SLUG}/tasks/TASK-42/recall`, () =>
        HttpResponse.json({
          task_id: 'TASK-42',
          assigned_agent: 'engineering_head',
          brief: 'Investigate spike',
          status: 'completed',
          output_summary: null,
          children: [],
        }),
      ),
    );
    mountAt(`/orgs/${SLUG}/audit/traces?task_id=TASK-42`);
    await waitFor(() =>
      expect(screen.getByText('Investigate spike')).toBeInTheDocument(),
    );
    expect(screen.queryByText(/Pick a task/i)).not.toBeInTheDocument();
  });
});
