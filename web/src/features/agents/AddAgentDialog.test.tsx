import { describe, expect, test, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { AppProvider } from '@/design-system/providers/AppProvider';
import { AddAgentDialog } from './AddAgentDialog';
import { agents as agentsApi, health as healthApi, runtimeExecutors as runtimeExecutorsApi, teams as teamsApi } from '@/lib/api';

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

/** Standard executor stubs for the happy path: one registered built-in
 *  (claude present=true), three unregistered built-ins, and one custom
 *  profile (openclaw, present=false — no binary registry entry). */
function stubStandardExecutors() {
  vi.spyOn(healthApi, 'getPrereqs').mockResolvedValue({
    prereqs: [
      { tool: 'claude', present: true, path: '/usr/local/bin/claude', hint: 'Register Claude Code via the onboarding prompt flow' },
      { tool: 'codex', present: false, path: null, hint: 'Register OpenAI Codex via the onboarding prompt flow' },
      { tool: 'opencode', present: false, path: null, hint: 'Register opencode via the onboarding prompt flow' },
      { tool: 'pi', present: false, path: null, hint: 'Register Pi via the onboarding prompt flow' },
      // Custom profile that appears in prereqs because it's in the registry.
      { tool: 'openclaw', present: false, path: null, hint: "Register the 'openclaw' CLI via the onboarding prompt flow." },
    ],
  });
  vi.spyOn(runtimeExecutorsApi, 'listRuntimeProfiles').mockResolvedValue({
    profiles: [
      { name: 'openclaw', command: 'openclaw', adapter: 'pi', present: false, path: null },
    ],
  });
}

function stubNoExecutors() {
  vi.spyOn(healthApi, 'getPrereqs').mockResolvedValue({
    prereqs: [
      { tool: 'claude', present: false, path: null, hint: 'Register Claude Code via the onboarding prompt flow' },
      { tool: 'codex', present: false, path: null, hint: 'Register OpenAI Codex via the onboarding prompt flow' },
      { tool: 'opencode', present: false, path: null, hint: 'Register opencode via the onboarding prompt flow' },
      { tool: 'pi', present: false, path: null, hint: 'Register Pi via the onboarding prompt flow' },
    ],
  });
  vi.spyOn(runtimeExecutorsApi, 'listRuntimeProfiles').mockResolvedValue({
    profiles: [],
  });
}

function stubExecutorsApiError() {
  vi.spyOn(healthApi, 'getPrereqs').mockRejectedValue(new Error('Network error'));
  // runtime/profiles succeeds (shouldn't matter — error state is OR).
  vi.spyOn(runtimeExecutorsApi, 'listRuntimeProfiles').mockResolvedValue({
    profiles: [],
  });
}

beforeEach(() => {
  vi.restoreAllMocks();
  vi.spyOn(teamsApi, 'listTeams').mockResolvedValue({
    teams: [
      { name: 'engineering', manager: 'engineering_head', workers: [] },
      { name: 'content', manager: 'content_manager', workers: [] },
    ],
  });
  // Default: standard executor set.
  stubStandardExecutors();
});

describe('AddAgentDialog', () => {
  // ── pre-existing tests, updated for new executor behavior ──────────────

  test('worker branch: team dropdown populated from useTeamsList', async () => {
    renderDialog();
    await waitFor(() =>
      expect(screen.getByRole('option', { name: 'engineering' })).toBeInTheDocument(),
    );
  });

  test('manager branch: new_team auto-tracks name until edited', async () => {
    const user = userEvent.setup();
    renderDialog();

    await waitFor(() => screen.getByRole('option', { name: 'claude' }));
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

    await waitFor(() => screen.getByRole('option', { name: 'claude' }));
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

  // ── NEW executor tests ─────────────────────────────────────────────────

  test('registered built-in (claude) is selectable and submitted; unregistered built-ins are disabled', async () => {
    const user = userEvent.setup();
    const spy = vi.spyOn(agentsApi, 'createAgent').mockResolvedValue({
      name: 'w1', team: 'engineering', role: 'worker',
    });
    renderDialog();

    await waitFor(() => screen.getByRole('option', { name: 'engineering' }));

    // Registered built-in is selectable.
    const executorSelect = screen.getByLabelText(/executor/i) as HTMLSelectElement;
    expect(executorSelect.value).toBe('claude');

    // Unregistered built-ins are present but disabled.
    const codexOpt = screen.getByRole('option', { name: 'codex (not registered)' }) as HTMLOptionElement;
    expect(codexOpt.disabled).toBe(true);
    const opencodeOpt = screen.getByRole('option', { name: 'opencode (not registered)' }) as HTMLOptionElement;
    expect(opencodeOpt.disabled).toBe(true);
    const piOpt = screen.getByRole('option', { name: 'pi (not registered)' }) as HTMLOptionElement;
    expect(piOpt.disabled).toBe(true);

    // Fill the form and submit.
    await user.selectOptions(screen.getByLabelText(/team/i), 'engineering');
    await user.type(screen.getByLabelText(/^name$/i), 'alpha_w1');
    await user.type(screen.getByLabelText(/description/i), 'desc');
    await user.type(screen.getByLabelText(/system prompt/i), 'prompt');
    await user.click(screen.getByRole('button', { name: /create/i }));

    await waitFor(() =>
      expect(spy).toHaveBeenCalledWith('test', expect.objectContaining({
        executor: 'claude',
      })),
    );
  });

  test('custom CLI profile with present=false is displayed, selectable, and submitted', async () => {
    const user = userEvent.setup();
    const spy = vi.spyOn(agentsApi, 'createAgent').mockResolvedValue({
      name: 'w1', team: 'engineering', role: 'worker',
    });
    renderDialog();

    await waitFor(() => screen.getByRole('option', { name: 'engineering' }));

    // Custom profile appears with (custom) suffix.
    const customOpt = screen.getByRole('option', { name: 'openclaw (custom)' }) as HTMLOptionElement;
    expect(customOpt).toBeInTheDocument();
    expect(customOpt.disabled).toBe(false);

    // Select it and submit.
    await user.selectOptions(screen.getByLabelText(/executor/i), 'openclaw');
    await user.selectOptions(screen.getByLabelText(/team/i), 'engineering');
    await user.type(screen.getByLabelText(/^name$/i), 'alpha_w1');
    await user.type(screen.getByLabelText(/description/i), 'desc');
    await user.type(screen.getByLabelText(/system prompt/i), 'prompt');
    await user.click(screen.getByRole('button', { name: /create/i }));

    await waitFor(() =>
      expect(spy).toHaveBeenCalledWith('test', expect.objectContaining({
        executor: 'openclaw',
      })),
    );
  });

  test('no registered executors disables Create with truthful guidance', async () => {
    vi.restoreAllMocks();
    vi.spyOn(teamsApi, 'listTeams').mockResolvedValue({
      teams: [{ name: 'engineering', manager: 'engineering_head', workers: [] }],
    });
    stubNoExecutors();

    const user = userEvent.setup();
    renderDialog();

    // Empty-state message references unregistered built-ins.
    await waitFor(() =>
      expect(screen.getByText(/no executors are registered/i)).toBeInTheDocument(),
    );
    expect(screen.getByText(/claude, codex, opencode, pi/i)).toBeInTheDocument();
    expect(screen.getByText(/register one via settings/i)).toBeInTheDocument();

    // Fill everything EXCEPT the (missing) executor selector.
    await user.selectOptions(screen.getByLabelText(/team/i), 'engineering');
    await user.type(screen.getByLabelText(/^name$/i), 'alpha_w1');
    await user.type(screen.getByLabelText(/description/i), 'desc');
    await user.type(screen.getByLabelText(/system prompt/i), 'prompt');

    // Create stays disabled (no executor to select).
    expect(screen.getByRole('button', { name: /create/i })).toBeDisabled();
  });

  test('API-unavailable disables Create — no invented fallback', async () => {
    vi.restoreAllMocks();
    vi.spyOn(teamsApi, 'listTeams').mockResolvedValue({
      teams: [{ name: 'engineering', manager: 'engineering_head', workers: [] }],
    });
    stubExecutorsApiError();

    const user = userEvent.setup();
    renderDialog();

    await waitFor(() =>
      expect(screen.getByText(/could not load the executor list/i)).toBeInTheDocument(),
    );

    await user.selectOptions(screen.getByLabelText(/team/i), 'engineering');
    await user.type(screen.getByLabelText(/^name$/i), 'alpha_w1');
    await user.type(screen.getByLabelText(/description/i), 'desc');
    await user.type(screen.getByLabelText(/system prompt/i), 'prompt');

    // No executor dropdown — Create is disabled.
    expect(screen.getByRole('button', { name: /create/i })).toBeDisabled();
  });
});
