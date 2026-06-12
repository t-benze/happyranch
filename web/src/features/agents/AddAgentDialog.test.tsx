import { describe, expect, test, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { AppProvider } from '@/design-system/providers/AppProvider';
import { AddAgentDialog } from './AddAgentDialog';
import { agents as agentsApi, teams as teamsApi } from '@/lib/api';

function renderDialog(props: { open?: boolean; onOpenChange?: (v: boolean) => void } = {}) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={['/orgs/test/agents']}>
        <Routes>
          <Route
            path="/orgs/:slug/agents"
            element={
              <AppProvider client={qc}>
                <AddAgentDialog open={props.open ?? true} onOpenChange={props.onOpenChange ?? (() => {})} />
              </AppProvider>
            }
          />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  vi.restoreAllMocks();
  vi.spyOn(teamsApi, 'listTeams').mockResolvedValue({
    teams: [
      { name: 'engineering', manager: 'engineering_head', workers: [] },
      { name: 'content', manager: 'content_manager', workers: [] },
    ],
  });
});

describe('AddAgentDialog', () => {
  test('worker branch: team dropdown populated from useTeamsList', async () => {
    renderDialog();
    await waitFor(() =>
      expect(screen.getByRole('option', { name: 'engineering' })).toBeInTheDocument(),
    );
  });

  test('manager branch: new_team auto-tracks name until edited', async () => {
    const user = userEvent.setup();
    renderDialog();

    await user.click(screen.getByLabelText(/manager/i));
    await user.type(screen.getByLabelText(/^name$/i), 'delta_head');
    const teamField = screen.getByLabelText(/new team name/i) as HTMLInputElement;
    expect(teamField.value).toBe('delta');

    // Manually editing the team field stops the linking.
    await user.clear(teamField);
    await user.type(teamField, 'manual');
    await user.clear(screen.getByLabelText(/^name$/i));
    await user.type(screen.getByLabelText(/^name$/i), 'omega_head');
    expect(teamField.value).toBe('manual'); // did NOT re-link
  });

  test('worker submit sends team-shaped body', async () => {
    const user = userEvent.setup();
    const spy = vi.spyOn(agentsApi, 'createAgent').mockResolvedValue({
      name: 'w1', team: 'engineering', role: 'worker',
    });
    renderDialog();
    await waitFor(() => screen.getByRole('option', { name: 'engineering' }));

    await user.type(screen.getByLabelText(/^name$/i), 'alpha_w1');
    await user.selectOptions(screen.getByLabelText(/team/i), 'engineering');
    await user.type(screen.getByLabelText(/description/i), 'desc');
    await user.type(screen.getByLabelText(/system prompt/i), 'prompt');
    await user.click(screen.getByRole('button', { name: /create/i }));

    await waitFor(() =>
      expect(spy).toHaveBeenCalledWith('test', expect.objectContaining({
        name: 'alpha_w1',
        role: 'worker',
        team: 'engineering',
        executor: 'claude',
      })),
    );
    expect(spy.mock.calls[0][1]).not.toHaveProperty('new_team');
  });

  test('manager submit sends new_team-shaped body', async () => {
    const user = userEvent.setup();
    const spy = vi.spyOn(agentsApi, 'createAgent').mockResolvedValue({
      name: 'delta_head', team: 'delta', role: 'manager',
    });
    renderDialog();

    await user.click(screen.getByLabelText(/manager/i));
    await user.type(screen.getByLabelText(/^name$/i), 'delta_head');
    await user.type(screen.getByLabelText(/description/i), 'desc');
    await user.type(screen.getByLabelText(/system prompt/i), 'prompt');
    await user.click(screen.getByRole('button', { name: /create/i }));

    await waitFor(() =>
      expect(spy).toHaveBeenCalledWith('test', expect.objectContaining({
        name: 'delta_head',
        role: 'manager',
        new_team: 'delta',
      })),
    );
    expect(spy.mock.calls[0][1]).not.toHaveProperty('team');
  });

  test('worker branch with empty teams list disables Create', async () => {
    vi.spyOn(teamsApi, 'listTeams').mockResolvedValue({ teams: [] });
    const user = userEvent.setup();
    renderDialog();
    await waitFor(() => screen.getByText(/no teams yet/i));
    await user.type(screen.getByLabelText(/^name$/i), 'alpha_w1');
    await user.type(screen.getByLabelText(/description/i), 'desc');
    await user.type(screen.getByLabelText(/system prompt/i), 'prompt');
    expect(screen.getByRole('button', { name: /create/i })).toBeDisabled();
  });
});
