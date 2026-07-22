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

/** Like renderDialog but returns the QueryClient so callers can invalidate
 *  and refetch queries on the SAME mounted instance for transition tests. */
function renderDialogWithClient(props: { open?: boolean; onOpenChange?: (v: boolean) => void } = {}) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const result = render(
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
  return { ...result, qc };
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

  test('non-four-name built-in executor (e.g. "gemini") with present=true is selectable and can be submitted', async () => {
    vi.restoreAllMocks();
    vi.spyOn(teamsApi, 'listTeams').mockResolvedValue({
      teams: [{ name: 'engineering', manager: 'engineering_head', workers: [] }],
    });
    // A prereq executor whose name is NOT one of the historical four built-ins.
    vi.spyOn(healthApi, 'getPrereqs').mockResolvedValue({
      prereqs: [
        { tool: 'claude', present: false, path: null, hint: 'Register Claude Code' },
        { tool: 'gemini', present: true, path: '/opt/gemini/bin', hint: 'Register Gemini CLI' },
      ],
    });
    vi.spyOn(runtimeExecutorsApi, 'listRuntimeProfiles').mockResolvedValue({
      profiles: [],
    });

    const user = userEvent.setup();
    const spy = vi.spyOn(agentsApi, 'createAgent').mockResolvedValue({
      name: 'w1', team: 'engineering', role: 'worker',
    });
    renderDialog();

    await waitFor(() => screen.getByRole('option', { name: 'engineering' }));

    // "gemini" appears as a selectable built-in (not filtered by a hard-coded allow-list).
    const geminiOpt = screen.getByRole('option', { name: 'gemini' }) as HTMLOptionElement;
    expect(geminiOpt).toBeInTheDocument();
    expect(geminiOpt.disabled).toBe(false);

    // "claude" with present=false is in the unregistered section.
    const claudeOpt = screen.getByRole('option', { name: 'claude (not registered)' }) as HTMLOptionElement;
    expect(claudeOpt.disabled).toBe(true);

    // Select gemini and submit.
    await user.selectOptions(screen.getByLabelText(/executor/i), 'gemini');
    await user.selectOptions(screen.getByLabelText(/team/i), 'engineering');
    await user.type(screen.getByLabelText(/^name$/i), 'alpha_w1');
    await user.type(screen.getByLabelText(/description/i), 'desc');
    await user.type(screen.getByLabelText(/system prompt/i), 'prompt');
    await user.click(screen.getByRole('button', { name: /create/i }));

    await waitFor(() =>
      expect(spy).toHaveBeenCalledWith('test', expect.objectContaining({
        executor: 'gemini',
      })),
    );
  });

  test('query-refetch transition: stale executor disappears, replacement auto-selects, stale value not submitted', async () => {
    // ONE dialog mount, ONE QueryClient — no unmount/remount.
    const user = userEvent.setup();
    vi.restoreAllMocks();
    vi.spyOn(teamsApi, 'listTeams').mockResolvedValue({
      teams: [{ name: 'engineering', manager: 'engineering_head', workers: [] }],
    });
    vi.spyOn(healthApi, 'getPrereqs').mockResolvedValue({
      prereqs: [
        { tool: 'claude', present: true, path: '/usr/bin/claude', hint: '' },
      ],
    });
    vi.spyOn(runtimeExecutorsApi, 'listRuntimeProfiles').mockResolvedValue({
      profiles: [
        { name: 'openclaw', command: 'openclaw', adapter: 'pi', present: true, path: '/usr/bin/openclaw' },
      ],
    });

    const { qc } = renderDialogWithClient();
    await waitFor(() => screen.getByRole('option', { name: 'openclaw (custom)' }));

    // User selects openclaw — a stale value we'll later prove cannot be submitted.
    await user.selectOptions(screen.getByLabelText(/executor/i), 'openclaw');
    expect((screen.getByLabelText(/executor/i) as HTMLSelectElement).value).toBe('openclaw');

    // Phase B: update the mocks so openclaw disappears while claude remains.
    // The dialog stays mounted — we invalidate the executor queries and refetch.
    vi.mocked(healthApi.getPrereqs).mockResolvedValue({
      prereqs: [
        { tool: 'claude', present: true, path: '/usr/bin/claude', hint: '' },
      ],
    });
    vi.mocked(runtimeExecutorsApi.listRuntimeProfiles).mockResolvedValue({
      profiles: [], // openclaw unregistered
    });

    const spy = vi.spyOn(agentsApi, 'createAgent').mockResolvedValue({
      name: 'w1', team: 'engineering', role: 'worker',
    });

    // Refetch the executor data while the dialog is still mounted.
    await qc.invalidateQueries({ queryKey: ['health', 'prereqs'] });
    await qc.invalidateQueries({ queryKey: ['runtime-profiles'] });

    // The effect should auto-select claude (the only remaining selectable option).
    await waitFor(() => {
      expect((screen.getByLabelText(/executor/i) as HTMLSelectElement).value).toBe('claude');
    });

    // openclaw (custom) is no longer in the selectable list.
    expect(() => screen.getByRole('option', { name: 'openclaw (custom)' })).toThrow();

    // Fill remaining fields and submit.
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
    // Prove openclaw was NEVER submitted.
    const calls = spy.mock.calls as Array<[string, { executor: string }]>;
    expect(calls.every(([_, body]) => body.executor !== 'openclaw')).toBe(true);
  });

  test('custom executor response appears in AgentSummary without type error', async () => {
    // Verify the type-widening works: an AgentSummary with a custom executor
    // name (not one of the 4 builtins) is accepted at runtime.
    vi.restoreAllMocks();
    vi.spyOn(teamsApi, 'listTeams').mockResolvedValue({
      teams: [{ name: 'engineering', manager: 'engineering_head', workers: [] }],
    });
    vi.spyOn(healthApi, 'getPrereqs').mockResolvedValue({
      prereqs: [
        { tool: 'claude', present: true, path: '/usr/local/bin/claude', hint: '' },
      ],
    });
    // A custom profile with a non-four-name executor.
    vi.spyOn(runtimeExecutorsApi, 'listRuntimeProfiles').mockResolvedValue({
      profiles: [
        { name: 'my-runner', command: 'my-runner', adapter: 'pi', present: false, path: null },
      ],
    });

    const spy = vi.spyOn(agentsApi, 'createAgent').mockResolvedValue({
      name: 'w1',
      team: 'engineering',
      role: 'worker',
    });
    const user = userEvent.setup();
    renderDialog();

    await waitFor(() => screen.getByRole('option', { name: 'engineering' }));

    // The custom runner appears and is selectable.
    const runnerOpt = screen.getByRole('option', { name: 'my-runner (custom)' }) as HTMLOptionElement;
    expect(runnerOpt).toBeInTheDocument();
    expect(runnerOpt.disabled).toBe(false);

    // Select and submit the custom runner.
    await user.selectOptions(screen.getByLabelText(/executor/i), 'my-runner');
    await user.selectOptions(screen.getByLabelText(/team/i), 'engineering');
    await user.type(screen.getByLabelText(/^name$/i), 'alpha_w1');
    await user.type(screen.getByLabelText(/description/i), 'desc');
    await user.type(screen.getByLabelText(/system prompt/i), 'prompt');
    await user.click(screen.getByRole('button', { name: /create/i }));

    await waitFor(() =>
      expect(spy).toHaveBeenCalledWith('test', expect.objectContaining({
        executor: 'my-runner',
      })),
    );
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
