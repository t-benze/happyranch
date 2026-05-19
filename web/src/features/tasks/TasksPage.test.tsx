import { screen, waitFor } from '@testing-library/react';
import { http, HttpResponse } from 'msw';
import { describe, expect, test } from 'vitest';
import { AppRoutes } from '@/routes';
import { renderWithProviders } from '@/test/render';
import { server } from '@/test/server';

const SLUG = 'hk-macau-tourism';

function mountAt(route: string) {
  server.use(
    http.get('/api/v1/orgs', () =>
      HttpResponse.json({ orgs: [{ slug: SLUG, root: '/x' }] }),
    ),
  );
  return renderWithProviders(<AppRoutes />, { route });
}

const TASK = {
  task_id: 'TASK-0091',
  team: 'content',
  brief: 'Draft Hong Kong visa guide v2',
  status: 'in_progress',
  block_kind: null,
  parent_task_id: null,
  revisit_of_task_id: null,
  created_at: '2026-05-18T10:00:00Z',
  updated_at: '2026-05-18T10:06:12Z',
  closed_at: null,
  cancelled_at: null,
  session_timeout_seconds: null,
};

describe('TasksPage — read path', () => {
  test('renders the inbox with fixture tasks', async () => {
    sessionStorage.setItem('grassland.token', 'tok');
    server.use(
      http.get(`/api/v1/orgs/${SLUG}/tasks`, () =>
        HttpResponse.json({ tasks: [TASK] }),
      ),
    );
    mountAt(`/orgs/${SLUG}/tasks`);
    await waitFor(() =>
      expect(screen.getByText(/Draft Hong Kong visa guide/)).toBeInTheDocument(),
    );
  });

  test('renders filter sidebar groups', async () => {
    sessionStorage.setItem('grassland.token', 'tok');
    server.use(
      http.get(`/api/v1/orgs/${SLUG}/tasks`, () =>
        HttpResponse.json({ tasks: [TASK] }),
      ),
    );
    mountAt(`/orgs/${SLUG}/tasks`);
    await waitFor(() => {
      expect(screen.getByText(/Status/i)).toBeInTheDocument();
      expect(screen.getByText(/Team/i)).toBeInTheDocument();
    });
  });
});
