import { screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { http, HttpResponse } from 'msw';
import { Route, Routes } from 'react-router-dom';
import { describe, expect, test } from 'vitest';
import type { AssistantStatus } from '@/lib/api/types';
import { renderWithProviders } from '@/test/render';
import { server } from '@/test/server';
import { AssistantSection } from './AssistantSection';

const SLUG = 'alpha';

function stubStatus(status: AssistantStatus) {
  server.use(http.get('/api/v1/assistant/status', () => HttpResponse.json(status)));
}

function render() {
  sessionStorage.setItem('happyranch.token', 'tok');
  renderWithProviders(
    <Routes>
      <Route path="/orgs/:slug/settings/assistant" element={<AssistantSection />} />
    </Routes>,
    { route: `/orgs/${SLUG}/settings/assistant` },
  );
}

describe('AssistantSection (Settings → Assistant)', () => {
  test('configured: shows status and the register form', async () => {
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

    // The full register flow now lives here.
    expect(screen.getByRole('region', { name: /Register executor/i })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /^Register$/i })).toBeInTheDocument();

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
  });

  test('stale_or_broken: shows the detail and a Repair action', async () => {
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
