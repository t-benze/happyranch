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

const SCRIPT = {
  id: 'SR-0001',
  task_id: 'TASK-0042',
  agent_name: 'engineering_head',
  title: 'Clean up stale Docker images',
  rationale: 'Disk usage is above 90% on the build server.',
  script_text: 'docker image prune -af',
  interpreter: 'bash',
  cwd_hint: null,
  status: 'pending',
  exit_code: null,
  stdout_head: null,
  stderr_head: null,
  stdout_path: null,
  stderr_path: null,
  duration_ms: null,
  started_at: null,
  finished_at: null,
  reviewed_at: null,
  reviewed_by: null,
  reject_reason: null,
  cwd_resolved: null,
  timeout_seconds: 300,
  created_at: '2026-05-23T12:00:00Z',
};

describe('ScriptsPage — read path', () => {
  test('renders empty state when no scripts', async () => {
    sessionStorage.setItem('grassland.token', 'tok');
    server.use(
      http.get(`/api/v1/orgs/${SLUG}/scripts/`, () =>
        HttpResponse.json({ scripts: [] }),
      ),
    );
    mountAt(`/orgs/${SLUG}/scripts`);
    await waitFor(() =>
      expect(screen.getByText(/No script requests/i)).toBeInTheDocument(),
    );
  });

  test('renders script cards when list returns data', async () => {
    sessionStorage.setItem('grassland.token', 'tok');
    server.use(
      http.get(`/api/v1/orgs/${SLUG}/scripts/`, () =>
        HttpResponse.json({ scripts: [SCRIPT] }),
      ),
    );
    mountAt(`/orgs/${SLUG}/scripts`);
    await waitFor(() =>
      expect(screen.getByText('Clean up stale Docker images')).toBeInTheDocument(),
    );
    expect(screen.getByText('SR-0001')).toBeInTheDocument();
    expect(screen.getByText('engineering_head')).toBeInTheDocument();
  });

  test('renders filter sidebar with Status group', async () => {
    sessionStorage.setItem('grassland.token', 'tok');
    server.use(
      http.get(`/api/v1/orgs/${SLUG}/scripts/`, () =>
        HttpResponse.json({ scripts: [SCRIPT] }),
      ),
    );
    mountAt(`/orgs/${SLUG}/scripts`);
    await waitFor(() => {
      expect(screen.getByText(/Status/i)).toBeInTheDocument();
    });
  });
});
