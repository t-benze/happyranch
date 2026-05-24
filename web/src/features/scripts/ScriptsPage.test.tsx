import { screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { http, HttpResponse } from 'msw';
import { describe, expect, test } from 'vitest';
import { AppRoutes } from '@/routes';
import { renderWithProviders } from '@/test/render';
import { server } from '@/test/server';
import type { ScriptRequest } from '@/lib/api/types';

const SLUG = 'hk-macau-tourism';

function mountAt(route: string) {
  server.use(
    http.get('/api/v1/orgs', () =>
      HttpResponse.json({ orgs: [{ slug: SLUG, root: '/x' }] }),
    ),
  );
  return renderWithProviders(<AppRoutes />, { route });
}

const SCRIPT: ScriptRequest = {
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

describe('ScriptDetailPane + RejectScriptDialog — write path', () => {
  function stubDetailHandlers() {
    server.use(
      http.get('/api/v1/orgs', () =>
        HttpResponse.json({ orgs: [{ slug: SLUG, root: '/x' }] }),
      ),
      http.get(`/api/v1/orgs/${SLUG}/scripts/`, () =>
        HttpResponse.json({ scripts: [SCRIPT] }),
      ),
      http.get(`/api/v1/orgs/${SLUG}/scripts/SR-0001`, () =>
        HttpResponse.json(SCRIPT),
      ),
    );
  }

  test('detail drawer renders title, rationale, script, and action bar for pending SR', async () => {
    sessionStorage.setItem('grassland.token', 'tok');
    stubDetailHandlers();
    mountAt(`/orgs/${SLUG}/scripts/SR-0001`);
    // Title and rationale appear in both the card and the drawer — use getAllByText
    await waitFor(() =>
      expect(screen.getAllByText('Clean up stale Docker images').length).toBeGreaterThanOrEqual(1),
    );
    expect(screen.getAllByText(/Disk usage is above 90%/i).length).toBeGreaterThanOrEqual(1);
    expect(screen.getByText('docker image prune -af')).toBeInTheDocument();
    // Action bar should be visible for pending SR
    expect(screen.getByRole('button', { name: /Reject/i })).toBeInTheDocument();
  });

  test('RejectScriptDialog submits reason and calls POST reject endpoint', async () => {
    sessionStorage.setItem('grassland.token', 'tok');
    stubDetailHandlers();
    let capturedBody: unknown = null;
    server.use(
      http.post(`/api/v1/orgs/${SLUG}/scripts/SR-0001/reject`, async ({ request: req }) => {
        capturedBody = await req.json();
        return HttpResponse.json({ ...SCRIPT, status: 'rejected', reject_reason: 'Too risky' });
      }),
    );

    const user = userEvent.setup();
    mountAt(`/orgs/${SLUG}/scripts/SR-0001`);

    // Click Reject to open dialog
    await user.click(await screen.findByRole('button', { name: /Reject/i }));

    // Dialog should open
    await waitFor(() =>
      expect(screen.getByRole('dialog')).toBeInTheDocument(),
    );

    // Type a reason
    await user.type(screen.getByPlaceholderText(/Reason \(required/i), 'Too risky');

    // Submit
    await user.click(screen.getByRole('button', { name: /^Reject$/ }));

    await waitFor(() => {
      expect(capturedBody).toEqual({ reason: 'Too risky' });
    });
  });

  test('reject button is disabled when reason is empty', async () => {
    sessionStorage.setItem('grassland.token', 'tok');
    stubDetailHandlers();

    const user = userEvent.setup();
    mountAt(`/orgs/${SLUG}/scripts/SR-0001`);

    await user.click(await screen.findByRole('button', { name: /Reject/i }));

    await waitFor(() =>
      expect(screen.getByRole('dialog')).toBeInTheDocument(),
    );

    // The Reject submit button should be disabled when reason is empty
    const submitBtn = screen.getByRole('button', { name: /^Reject$/ });
    expect(submitBtn).toBeDisabled();
  });

  test('detail drawer shows reject reason section for rejected SR', async () => {
    sessionStorage.setItem('grassland.token', 'tok');
    const rejectedScript = {
      ...SCRIPT,
      status: 'rejected' as const,
      reject_reason: 'Too risky to run',
    };
    server.use(
      http.get('/api/v1/orgs', () =>
        HttpResponse.json({ orgs: [{ slug: SLUG, root: '/x' }] }),
      ),
      http.get(`/api/v1/orgs/${SLUG}/scripts/`, () =>
        HttpResponse.json({ scripts: [rejectedScript] }),
      ),
      http.get(`/api/v1/orgs/${SLUG}/scripts/SR-0001`, () =>
        HttpResponse.json(rejectedScript),
      ),
    );
    mountAt(`/orgs/${SLUG}/scripts/SR-0001`);
    await waitFor(() =>
      expect(screen.getByText('Too risky to run')).toBeInTheDocument(),
    );
    // Action bar should NOT be visible for rejected SR
    expect(screen.queryByRole('button', { name: /Reject/i })).not.toBeInTheDocument();
  });
});

describe('OutputPanel', () => {
  function stubDetailForStatus(script: typeof SCRIPT) {
    server.use(
      http.get('/api/v1/orgs', () =>
        HttpResponse.json({ orgs: [{ slug: SLUG, root: '/x' }] }),
      ),
      http.get(`/api/v1/orgs/${SLUG}/scripts/`, () =>
        HttpResponse.json({ scripts: [script] }),
      ),
      http.get(`/api/v1/orgs/${SLUG}/scripts/SR-0001`, () =>
        HttpResponse.json(script),
      ),
    );
  }

  test('renders no output section for pending SR', async () => {
    sessionStorage.setItem('grassland.token', 'tok');
    stubDetailForStatus(SCRIPT);
    mountAt(`/orgs/${SLUG}/scripts/SR-0001`);
    // Wait for detail pane to load
    await waitFor(() =>
      expect(screen.getAllByText('Clean up stale Docker images').length).toBeGreaterThanOrEqual(1),
    );
    // OutputPanel returns null for pending — no "Output" heading
    expect(screen.queryByText(/^Output$/i)).not.toBeInTheDocument();
  });

  test('renders stdout and stderr pre blocks for completed SR', async () => {
    sessionStorage.setItem('grassland.token', 'tok');
    const completedScript = {
      ...SCRIPT,
      status: 'completed' as const,
      exit_code: 0,
      finished_at: '2026-05-23T12:05:00Z',
    };
    stubDetailForStatus(completedScript);
    server.use(
      http.get(`/api/v1/orgs/${SLUG}/scripts/SR-0001/output`, () =>
        HttpResponse.json({
          stdout: 'Deleted 3 images\n',
          stderr: '',
          truncated_stdout: false,
          truncated_stderr: false,
          total_stdout_bytes: 18,
          total_stderr_bytes: 0,
        }),
      ),
    );
    mountAt(`/orgs/${SLUG}/scripts/SR-0001`);
    await waitFor(() =>
      expect(screen.getByText('Deleted 3 images')).toBeInTheDocument(),
    );
    // Both stdout and stderr headings should be present
    expect(screen.getByText(/^stdout$/i)).toBeInTheDocument();
    expect(screen.getByText(/^stderr$/i)).toBeInTheDocument();
    // Empty stderr renders as '(empty)'
    expect(screen.getByText('(empty)')).toBeInTheDocument();
  });

  test('renders no output section for rejected SR', async () => {
    sessionStorage.setItem('grassland.token', 'tok');
    const rejectedScript = {
      ...SCRIPT,
      status: 'rejected' as const,
      reject_reason: 'Not allowed',
    };
    stubDetailForStatus(rejectedScript);
    mountAt(`/orgs/${SLUG}/scripts/SR-0001`);
    await waitFor(() =>
      expect(screen.getByText('Not allowed')).toBeInTheDocument(),
    );
    expect(screen.queryByText(/^Output$/i)).not.toBeInTheDocument();
  });
});
