import { screen } from '@testing-library/react';
import { http, HttpResponse } from 'msw';
import { describe, expect, test, vi } from 'vitest';
import type { AssistantStatus } from '@/lib/api/types';
import { AppRoutes } from '@/routes';
import { renderWithProviders } from '@/test/render';
import { server } from '@/test/server';

// The terminal pulls in xterm + a live WebSocket; stub it so page tests stay
// focused on the full-terminal vs. unconfigured-prompt branches (the PTY
// protocol helpers are unit-tested in lib/api/assistant.test.ts). Mock by the
// same specifier the page imports it with so the mock actually intercepts.
vi.mock('./AssistantTerminal', () => ({
  AssistantTerminal: () => <div data-testid="assistant-terminal" />,
}));

const SLUG = 'alpha';

function stubOrgs() {
  server.use(
    http.get('/api/v1/orgs', () =>
      HttpResponse.json({ orgs: [{ slug: SLUG, root: '/x' }] }),
    ),
  );
}

function stubStatus(status: AssistantStatus) {
  server.use(http.get('/api/v1/assistant/status', () => HttpResponse.json(status)));
}

function render() {
  sessionStorage.setItem('happyranch.token', 'tok');
  stubOrgs();
  renderWithProviders(<AppRoutes />, { route: `/orgs/${SLUG}/assistant` });
}

describe('SystemAssistantPage', () => {
  test('configured: mounts the full-viewport terminal and no config UI', async () => {
    stubStatus({
      state: 'configured',
      selected_executor: 'claude',
      workspace_path: '/rt/system/assistant/workspace',
      detail: null,
    });

    render();

    expect(await screen.findByTestId('assistant-terminal')).toBeInTheDocument();
    // All config/setup now lives in Settings → Assistant; none of it here.
    expect(screen.queryByText(/System Assistant/i)).not.toBeInTheDocument();
    expect(
      screen.queryByRole('button', { name: /^Register$/i }),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByRole('region', { name: /Assistant status/i }),
    ).not.toBeInTheDocument();
  });

  test('uninitialized: shows the "not set up" prompt linking to Settings, no terminal', async () => {
    stubStatus({
      state: 'uninitialized',
      selected_executor: null,
      workspace_path: null,
      detail: null,
    });

    render();

    expect(await screen.findByText(/Assistant not set up/i)).toBeInTheDocument();
    const link = screen.getByRole('link', { name: /Settings → Assistant/i });
    expect(link).toHaveAttribute('href', `/orgs/${SLUG}/settings/assistant`);
    expect(screen.queryByTestId('assistant-terminal')).not.toBeInTheDocument();
  });

  test('stale_or_broken: also shows the "not set up" prompt, no terminal', async () => {
    stubStatus({
      state: 'stale_or_broken',
      selected_executor: 'codex',
      workspace_path: '/rt/system/assistant/workspace',
      detail: 'workspace missing AGENTS.md',
    });

    render();

    expect(await screen.findByText(/Assistant not set up/i)).toBeInTheDocument();
    expect(screen.queryByTestId('assistant-terminal')).not.toBeInTheDocument();
  });

  test('status error: surfaces a readable alert, no terminal', async () => {
    server.use(
      http.get('/api/v1/assistant/status', () =>
        HttpResponse.json({ detail: 'boom' }, { status: 500 }),
      ),
    );

    render();

    expect(await screen.findByRole('alert')).toHaveTextContent(
      /Could not load assistant status/i,
    );
    expect(screen.queryByTestId('assistant-terminal')).not.toBeInTheDocument();
  });
});
