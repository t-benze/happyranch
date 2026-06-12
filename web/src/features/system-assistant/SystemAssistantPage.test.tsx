import { screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { http, HttpResponse } from 'msw';
import { describe, expect, test, vi } from 'vitest';
import type { AssistantStatus } from '@/lib/api/types';
import { AppRoutes } from '@/routes';
import { renderWithProviders } from '@/test/render';
import { server } from '@/test/server';

// The terminal pulls in xterm + a live WebSocket; stub it so page tests stay
// focused on status/setup/register behaviour (the PTY protocol helpers are
// unit-tested in lib/api/assistant.test.ts). Mock by the same specifier the
// page imports it with so the mock actually intercepts.
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
  test('renders configured status and mounts the terminal', async () => {
    stubStatus({
      state: 'configured',
      selected_executor: 'claude',
      workspace_path: '/rt/system/assistant/workspace',
      detail: null,
    });

    render();

    expect(await screen.findByText('Configured')).toBeInTheDocument();
    // "claude" also appears in the executor picker, so scope to the status card.
    const statusCard = screen.getByRole('region', { name: /Assistant status/i });
    expect(within(statusCard).getByText('claude')).toBeInTheDocument();
    expect(within(statusCard).getByText('/rt/system/assistant/workspace')).toBeInTheDocument();
    expect(screen.getByTestId('assistant-terminal')).toBeInTheDocument();
  });

  test('uninitialized: Initialize prepares the workspace and shows self-registration steps', async () => {
    stubStatus({
      state: 'uninitialized',
      selected_executor: null,
      workspace_path: null,
      detail: null,
    });
    server.use(
      http.post('/api/v1/assistant/init', () =>
        HttpResponse.json({
          state: 'uninitialized',
          selected_executor: null,
          workspace_path: '/rt/system/assistant/workspace',
          detail: null,
        }),
      ),
    );

    const user = userEvent.setup();
    render();

    await user.click(await screen.findByRole('button', { name: /Initialize workspace/i }));

    expect(await screen.findByText(/Self-registration/i)).toBeInTheDocument();
    // The terminal only mounts when configured.
    expect(screen.queryByTestId('assistant-terminal')).not.toBeInTheDocument();
  });

  test('stale_or_broken: shows the detail and a Repair action, no terminal', async () => {
    stubStatus({
      state: 'stale_or_broken',
      selected_executor: 'codex',
      workspace_path: '/rt/system/assistant/workspace',
      detail: 'workspace missing AGENTS.md',
    });

    render();

    expect(await screen.findByText('Stale or broken')).toBeInTheDocument();
    expect(screen.getByText('workspace missing AGENTS.md')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /^Repair$/i })).toBeInTheDocument();
    expect(screen.queryByTestId('assistant-terminal')).not.toBeInTheDocument();
  });

  test('surfaces the structural registration error verbatim', async () => {
    stubStatus({
      state: 'configured',
      selected_executor: 'claude',
      workspace_path: '/rt/system/assistant/workspace',
      detail: null,
    });
    server.use(
      http.post('/api/v1/assistant/register', () =>
        HttpResponse.json(
          {
            detail: {
              code: 'assistant_executable_not_found',
              executable: 'ghost-cli',
            },
          },
          { status: 400 },
        ),
      ),
    );

    const user = userEvent.setup();
    render();

    await user.type(await screen.findByLabelText(/^Command$/i), 'ghost-cli');
    await user.click(screen.getByRole('button', { name: /^Register$/i }));

    expect(await screen.findByRole('alert')).toHaveTextContent(
      'assistant_executable_not_found',
    );
  });
});
