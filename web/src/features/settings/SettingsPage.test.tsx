import { screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { http, HttpResponse } from 'msw';
import { describe, expect, test, beforeEach } from 'vitest';
import { AppRoutes } from '@/routes';
import { renderWithProviders } from '@/test/render';
import { server } from '@/test/server';

const SLUG = 'test-org';

const SETTINGS_PAYLOAD = {
  system: {
    claude_cli_path: { value: 'claude', restart_required: true },
    codex_cli_path: { value: 'codex', restart_required: true },
    opencode_cli_path: { value: 'opencode', restart_required: true },
    pi_cli_path: { value: 'pi', restart_required: true },
    session_timeout_seconds: { value: 1800, restart_required: true },
    max_orchestration_steps: { value: 50, restart_required: true },
    queue_workers: { value: 3, restart_required: true },
    protocol_dir: { value: 'protocol', restart_required: true },
  },
  org: {
    session_timeout_seconds: null,
    dreaming: {
      enabled: true,
      schedule: { time: '09:00', timezone: 'UTC' },
      catch_up_on_startup: false,
      agents: { mode: 'all', include: [], exclude: [] },
    },
    threads: {
      enabled: true,
      default_turn_cap: 5,
      invocation_timeout_seconds: null,
    },
  },
};

const AGENTS_PAYLOAD = {
  agents: [
    { name: 'dev_agent', team: 'engineering', role: 'worker', executor: 'claude', description: '', repos: {}, system_prompt: '' },
    { name: 'qa_engineer', team: 'engineering', role: 'worker', executor: 'codex', description: '', repos: {}, system_prompt: '' },
  ],
};

const TOKENS_PAYLOAD = {
  rollup: [
    { agent: 'dev_agent', total_tokens: 15000, input_tokens: 10000, output_tokens: 4000, cache_read_tokens: 1000, sessions: 3 },
    { agent: 'qa_engineer', total_tokens: 5000, input_tokens: 3000, output_tokens: 1500, cache_read_tokens: 500, sessions: 1 },
  ],
};

function stubBaseHandlers() {
  server.use(
    http.get('/api/v1/orgs', () =>
      HttpResponse.json({ orgs: [{ slug: SLUG, root: '/x' }] }),
    ),
    http.get(`/api/v1/orgs/${SLUG}/settings`, () =>
      HttpResponse.json(SETTINGS_PAYLOAD),
    ),
    http.get(`/api/v1/orgs/${SLUG}/agents`, () =>
      HttpResponse.json(AGENTS_PAYLOAD),
    ),
    http.get('/api/v1/assistant/status', () =>
      HttpResponse.json({ state: 'uninitialized', selected_executor: null, workspace_path: null, detail: null }),
    ),
    http.get(`/api/v1/orgs/${SLUG}/tokens`, () =>
      HttpResponse.json(TOKENS_PAYLOAD),
    ),
  );
}

function mountAt(route: string) {
  sessionStorage.setItem('happyranch.token', 'tok');
  return renderWithProviders(<AppRoutes />, { route });
}

describe('SettingsPage — sub-nav and routing', () => {
  beforeEach(() => {
    stubBaseHandlers();
  });

  test('iAC1: /settings is a real bookmarkable route with sub-nav', async () => {
    mountAt(`/orgs/${SLUG}/settings`);

    // Should redirect to /settings/assistant
    await waitFor(() =>
      expect(screen.getByText('System Assistant')).toBeInTheDocument(),
    );
  });

  test('sub-nav renders all six sections', async () => {
    mountAt(`/orgs/${SLUG}/settings/assistant`);

    await waitFor(() =>
      expect(screen.getByTestId('settings-content')).toBeInTheDocument(),
    );

    const content = screen.getByTestId('settings-content');
    expect(within(content).getByText('Assistant')).toBeInTheDocument();
    expect(within(content).getByText('System')).toBeInTheDocument();
    expect(within(content).getByText('Organization')).toBeInTheDocument();
    expect(within(content).getByText('Agents')).toBeInTheDocument();
    expect(within(content).getByText('Executors')).toBeInTheDocument();
    expect(within(content).getByText('Usage')).toBeInTheDocument();
  });

  test('sub-nav switches panels via navigation', async () => {
    mountAt(`/orgs/${SLUG}/settings/assistant`);

    await waitFor(() =>
      expect(screen.getByText('System Assistant')).toBeInTheDocument(),
    );

    const user = userEvent.setup();
    await user.click(screen.getByText('System'));

    // System section should show daemon-wide settings notice
    await waitFor(() =>
      expect(screen.getByText('Daemon-wide settings. These are read-only', { exact: false })).toBeInTheDocument(),
    );
    // Should show restart-required badge on protocol_dir
    expect(screen.getByText('Protocol dir')).toBeInTheDocument();
  });
});

describe('SettingsPage — System section', () => {
  beforeEach(() => {
    stubBaseHandlers();
  });

  test('shows system settings with restart-required badges', async () => {
    mountAt(`/orgs/${SLUG}/settings/system`);

    await waitFor(() =>
      expect(screen.getByText('Protocol dir')).toBeInTheDocument(),
    );

    // All 8 system fields are restart-required
    const badges = screen.getAllByText('Restart required');
    expect(badges.length).toBeGreaterThanOrEqual(7);
  });

  test('session_timeout_seconds shows restart-required badge', async () => {
    mountAt(`/orgs/${SLUG}/settings/system`);

    await waitFor(() =>
      expect(screen.getByText('Session timeout (s)')).toBeInTheDocument(),
    );

    // All 8 system fields (including session_timeout_seconds) are restart-required
    const restartBadges = screen.getAllByText('Restart required');
    expect(restartBadges.length).toBe(8);
  });
});

describe('SettingsPage — Organization section', () => {
  beforeEach(() => {
    stubBaseHandlers();
  });

  test('renders editable org settings form', async () => {
    mountAt(`/orgs/${SLUG}/settings/organization`);

    await waitFor(() =>
      expect(screen.getByTestId('settings-content')).toBeInTheDocument(),
    );

    const content = screen.getByTestId('settings-content');

    await waitFor(() =>
      expect(within(content).getByText('Session timeout (s)')).toBeInTheDocument(),
    );

    // All org fields show "Applies live" badge
    const liveBadges = within(content).getAllByText('Applies live');
    expect(liveBadges.length).toBeGreaterThanOrEqual(8); // timeout + dreaming fields + threads fields
  });

  test('Clean⇄Dirty: save bar appears when form is dirty', async () => {
    mountAt(`/orgs/${SLUG}/settings/organization`);

    await waitFor(() =>
      expect(screen.getByTestId('settings-content')).toBeInTheDocument(),
    );
    const content = screen.getByTestId('settings-content');

    await waitFor(() =>
      expect(within(content).getByText('Session timeout (s)')).toBeInTheDocument(),
    );

    // Save bar should NOT be visible when clean
    expect(screen.queryByText('Save changes')).not.toBeInTheDocument();
    expect(screen.queryByText('Discard')).not.toBeInTheDocument();

    const user = userEvent.setup();

    // Change the timeout field
    const timeoutInput = screen.getByPlaceholderText('use system default');
    await user.clear(timeoutInput);
    await user.type(timeoutInput, '60');

    // Save bar should appear
    await waitFor(() =>
      expect(screen.getByText('Save changes')).toBeInTheDocument(),
    );
    expect(screen.getByText('Discard')).toBeInTheDocument();
  });

  test('Discard reverts fields to last saved state', async () => {
    mountAt(`/orgs/${SLUG}/settings/organization`);

    await waitFor(() =>
      expect(screen.getByTestId('settings-content')).toBeInTheDocument(),
    );
    const content = screen.getByTestId('settings-content');

    await waitFor(() =>
      expect(within(content).getByText('Session timeout (s)')).toBeInTheDocument(),
    );

    const user = userEvent.setup();

    const timeoutInput = screen.getByPlaceholderText('use system default');
    await user.clear(timeoutInput);
    await user.type(timeoutInput, '60');

    await waitFor(() =>
      expect(within(content).getByText('Discard')).toBeInTheDocument(),
    );

    await user.click(within(content).getByText('Discard'));

    // Should revert to original empty state
    await waitFor(() =>
      expect(timeoutInput).toHaveValue(null),
    );

    // Save bar should disappear
    await waitFor(() =>
      expect(within(content).queryByText('Save changes')).not.toBeInTheDocument(),
    );
  });

  test('Save changes calls PUT /settings/org and shows success', async () => {
    let savedBody: unknown = null;
    server.use(
      http.put(`/api/v1/orgs/${SLUG}/settings/org`, async ({ request }) => {
        savedBody = await request.json();
        return HttpResponse.json(SETTINGS_PAYLOAD);
      }),
    );

    mountAt(`/orgs/${SLUG}/settings/organization`);

    await waitFor(() =>
      expect(screen.getByTestId('settings-content')).toBeInTheDocument(),
    );
    const content = screen.getByTestId('settings-content');

    await waitFor(() =>
      expect(within(content).getByText('Session timeout (s)')).toBeInTheDocument(),
    );

    const user = userEvent.setup();
    const timeoutInput = screen.getByPlaceholderText('use system default');
    await user.clear(timeoutInput);
    await user.type(timeoutInput, '90');

    await waitFor(() =>
      expect(within(content).getByText('Save changes')).toBeInTheDocument(),
    );

    await user.click(within(content).getByText('Save changes'));

    await waitFor(() =>
      expect(within(content).getByText('Saved. Changes will take effect within ~1 minute.', { exact: false })).toBeInTheDocument(),
    );

    expect(savedBody).toEqual(
      expect.objectContaining({ session_timeout_seconds: 90 }),
    );
  });

  test('Save error shows inline error message', async () => {
    server.use(
      http.put(`/api/v1/orgs/${SLUG}/settings/org`, () =>
        HttpResponse.json({ detail: 'Validation failed' }, { status: 422 }),
      ),
    );

    mountAt(`/orgs/${SLUG}/settings/organization`);

    await waitFor(() =>
      expect(screen.getByTestId('settings-content')).toBeInTheDocument(),
    );
    const content = screen.getByTestId('settings-content');

    await waitFor(() =>
      expect(within(content).getByText('Session timeout (s)')).toBeInTheDocument(),
    );

    const user = userEvent.setup();
    const timeoutInput = screen.getByPlaceholderText('use system default');
    await user.clear(timeoutInput);
    await user.type(timeoutInput, '90');

    await waitFor(() =>
      expect(within(content).getByText('Save changes')).toBeInTheDocument(),
    );

    await user.click(within(content).getByText('Save changes'));

    await waitFor(() =>
      expect(within(content).getByText('Save failed', { exact: false })).toBeInTheDocument(),
    );
  });

  test('iAC3: dreaming include/exclude inputs render roster-autocomplete', async () => {
    mountAt(`/orgs/${SLUG}/settings/organization`);

    await waitFor(() =>
      expect(screen.getByTestId('settings-content')).toBeInTheDocument(),
    );
    const content = screen.getByTestId('settings-content');

    await waitFor(() =>
      expect(within(content).getByText('Included agents')).toBeInTheDocument(),
    );
    expect(within(content).getByText('Excluded agents')).toBeInTheDocument();

    // Both include and exclude fields have the same placeholder; pick the include field
    const inputs = screen.getAllByPlaceholderText('add agents…');
    expect(inputs).toHaveLength(2);
    const includeInput = inputs[0];
    expect(includeInput).toHaveAttribute('autocomplete', 'off');

    // Typing should trigger suggestions from the roster
    const user = userEvent.setup();
    await user.click(includeInput);
    await user.type(includeInput, 'dev');

    // The autocomplete listbox should appear with matching agent
    await waitFor(() =>
      expect(screen.getByRole('listbox')).toBeInTheDocument(),
    );
    // Should contain dev_agent from the roster
    expect(screen.getByText('dev_agent')).toBeInTheDocument();
  });

  test('iAC3: selecting an agent from autocomplete commits as comma-separated token', async () => {
    mountAt(`/orgs/${SLUG}/settings/organization`);

    await waitFor(() =>
      expect(screen.getByTestId('settings-content')).toBeInTheDocument(),
    );
    const content = screen.getByTestId('settings-content');

    await waitFor(() =>
      expect(within(content).getByText('Included agents')).toBeInTheDocument(),
    );

    const inputs = screen.getAllByPlaceholderText('add agents…');
    const includeInput = inputs[0];
    const user = userEvent.setup();
    await user.click(includeInput);
    await user.type(includeInput, 'dev');

    // Wait for listbox to appear
    await waitFor(() =>
      expect(screen.getByRole('listbox')).toBeInTheDocument(),
    );

    // Click the matching agent to select it
    await user.click(screen.getByText('dev_agent'));

    // The field should now contain the selected agent
    await waitFor(() =>
      expect(includeInput).toHaveValue('dev_agent, '),
    );
  });
});

describe('SettingsPage — Agents section', () => {
  beforeEach(() => {
    stubBaseHandlers();
  });

  test('shows gap notice directing to Agents page', async () => {
    mountAt(`/orgs/${SLUG}/settings/agents`);

    await waitFor(() =>
      expect(screen.getByTestId('settings-content')).toBeInTheDocument(),
    );
    const content = screen.getByTestId('settings-content');

    await waitFor(() =>
      expect(within(content).getByText('Agent roster')).toBeInTheDocument(),
    );

    expect(
      within(content).getByText(/Agent configuration is not editable/i),
    ).toBeInTheDocument();

    // Link to Agents page
    expect(within(content).getByText('Agents page')).toHaveAttribute('href', '../agents');
  });

  test('Founder handle reads broadcast framing (iAC4)', async () => {
    mountAt(`/orgs/${SLUG}/settings/agents`);

    await waitFor(() =>
      expect(screen.getByTestId('settings-content')).toBeInTheDocument(),
    );
    const content = screen.getByTestId('settings-content');

    expect(within(content).getByText('Founder handle')).toBeInTheDocument();

    // iAC4: broadcast framing — no @mention routing promise
    expect(
      within(content).getByText('The handle agents reference when they broadcast to you.'),
    ).toBeInTheDocument();
    // Must NOT contain "route questions to you"
    expect(
      within(content).queryByText(/route.*questions/i),
    ).not.toBeInTheDocument();
  });
});

describe('SettingsPage — Executors section', () => {
  beforeEach(() => {
    stubBaseHandlers();
  });

  test('shows gap notice with read-only config info', async () => {
    mountAt(`/orgs/${SLUG}/settings/executors`);

    await waitFor(() =>
      expect(screen.getByText('Executor configuration')).toBeInTheDocument(),
    );

    expect(
      screen.getByText(/configured in the daemon config file/i),
    ).toBeInTheDocument();
  });
});

describe('SettingsPage — Usage section', () => {
  beforeEach(() => {
    stubBaseHandlers();
  });

  test('renders Usage (NOT Billing) with token data', async () => {
    mountAt(`/orgs/${SLUG}/settings/usage`);

    await waitFor(() =>
      expect(screen.getByTestId('settings-content')).toBeInTheDocument(),
    );
    const content = screen.getByTestId('settings-content');

    expect(within(content).getByText('Token consumption across the org.')).toBeInTheDocument();

    // Must NOT say "Billing"
    expect(within(content).queryByText('Billing')).not.toBeInTheDocument();

    // Shows token data
    await waitFor(() =>
      expect(within(content).getByText('15.0K')).toBeInTheDocument(),
    );
    expect(within(content).getByText('5.0K')).toBeInTheDocument();
  });

  test('shows stat cards for total tokens, cache reads, sessions, agents', async () => {
    mountAt(`/orgs/${SLUG}/settings/usage`);

    await waitFor(() =>
      expect(screen.getByTestId('settings-content')).toBeInTheDocument(),
    );
    const content = screen.getByTestId('settings-content');

    // Wait for loading to finish and data to appear
    await waitFor(() =>
      expect(within(content).getByText('Total Tokens')).toBeInTheDocument(),
    );
    expect(within(content).getByText('Cache Reads')).toBeInTheDocument();
    expect(within(content).queryAllByText('Sessions').length).toBeGreaterThanOrEqual(1);
    expect(within(content).getByText('Agents Active')).toBeInTheDocument();
  });
});

describe('SettingsPage — keyboard shortcuts', () => {
  beforeEach(() => {
    stubBaseHandlers();
  });

  test('⌘S shortcut keybinding hint visible when dirty', async () => {
    mountAt(`/orgs/${SLUG}/settings/organization`);

    await waitFor(() =>
      expect(screen.getByTestId('settings-content')).toBeInTheDocument(),
    );
    const content = screen.getByTestId('settings-content');

    await waitFor(() =>
      expect(within(content).getByText('Session timeout (s)')).toBeInTheDocument(),
    );

    const user = userEvent.setup();
    const timeoutInput = screen.getByPlaceholderText('use system default');
    await user.clear(timeoutInput);
    await user.type(timeoutInput, '99');

    await waitFor(() =>
      expect(within(content).getByText('⌘S to save')).toBeInTheDocument(),
    );
  });
});
