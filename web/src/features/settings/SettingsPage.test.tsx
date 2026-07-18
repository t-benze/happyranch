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
    // THR-061 seq79: the Usage sub-tab was removed (Usage now lives on /usage).
    expect(within(content).queryByText('Usage')).not.toBeInTheDocument();
  });

  test('SET-03: each sub-nav item renders a leading icon', async () => {
    mountAt(`/orgs/${SLUG}/settings/assistant`);

    await waitFor(() =>
      expect(screen.getByTestId('settings-content')).toBeInTheDocument(),
    );

    const content = screen.getByTestId('settings-content');
    const subnav = within(content).getByRole('complementary');

    for (const label of [
      'Assistant',
      'System',
      'Organization',
      'Agents',
      'Executors',
    ]) {
      const link = within(subnav).getByRole('link', { name: label });
      // Each sub-nav link carries a leading (decorative) icon SVG.
      expect(link.querySelector('svg')).not.toBeNull();
    }
  });

  test('sub-nav switches panels via navigation', async () => {
    mountAt(`/orgs/${SLUG}/settings/assistant`);

    await waitFor(() =>
      expect(screen.getByText('System Assistant')).toBeInTheDocument(),
    );

    // The AssistantDockHost (global ⌘K dock) is now mounted in AppShell;
    // wait for any async side-effects to settle before finding sub-nav.
    const user = userEvent.setup();
    const content = await screen.findByTestId('settings-content');
    await user.click(within(content).getByText('System'));

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
    expect(liveBadges.length).toBeGreaterThanOrEqual(7); // timeout + dreaming fields + threads fields (minus removed turn cap)

    // Default turn cap must NOT be rendered (THR-046 msg126)
    expect(within(content).queryByText('Default turn cap')).not.toBeInTheDocument();
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

  test('iAC3: non-roster agent name cannot be committed or saved', async () => {
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
      expect(within(content).getByText('Included agents')).toBeInTheDocument(),
    );

    const inputs = screen.getAllByPlaceholderText('add agents…');
    const includeInput = inputs[0];
    const user = userEvent.setup();
    await user.click(includeInput);

    // Type a non-roster name followed by comma (attempting to commit it as a token)
    await user.type(includeInput, 'non_existent,');

    // The non-roster token must NOT appear — it is rejected
    expect(includeInput).toHaveValue('');

    // Form must NOT be dirty since no valid change was made
    expect(within(content).queryByText('Save changes')).not.toBeInTheDocument();

    // Now add a valid roster agent via autocomplete
    await user.type(includeInput, 'dev');
    await waitFor(() =>
      expect(screen.getByRole('listbox')).toBeInTheDocument(),
    );
    await user.click(screen.getByText('dev_agent'));

    await waitFor(() =>
      expect(includeInput).toHaveValue('dev_agent, '),
    );

    // Save and verify the patch does NOT include the non-roster name
    await user.click(within(content).getByText('Save changes'));

    await waitFor(() =>
      expect(within(content).getByText('Saved. Changes will take effect within ~1 minute.', { exact: false })).toBeInTheDocument(),
    );

    expect(savedBody).toBeDefined();
    const body = savedBody as { dreaming: { agents: { include: string[] } } };
    expect(body.dreaming.agents.include).toContain('dev_agent');
    expect(body.dreaming.agents.include).not.toContain('non_existent');
  });

  test('iAC3: non-roster token with NO trailing comma does not dirty the form', async () => {
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
      expect(within(content).getByText('Included agents')).toBeInTheDocument(),
    );

    const inputs = screen.getAllByPlaceholderText('add agents…');
    const includeInput = inputs[0];
    const user = userEvent.setup();
    await user.click(includeInput);

    // Type a non-roster name with NO trailing comma (actively-typed token)
    await user.type(includeInput, 'non_existent');

    // The input may still show the text (RecipientsInput preserves the
    // actively-typed last token for autocomplete), but the form MUST stay clean
    expect(within(content).queryByText('Save changes')).not.toBeInTheDocument();
    expect(within(content).queryByText('Discard')).not.toBeInTheDocument();

    // Simulate save attempt via keyboard shortcut (Cmd+S / Ctrl+S fires
    // handleSave, which calls buildPatch). Since the form is clean, no save
    // should actually be dispatched. Verify by changing a legitimate field
    // first, saving, then checking the body.
    // Instead, change a valid field to make the form dirty, then save and
    // confirm the non-roster token is NOT in the saved body.

    // Clear the non-roster input and add a valid roster agent
    await user.clear(includeInput);
    await user.type(includeInput, 'dev');
    await waitFor(() =>
      expect(screen.getByRole('listbox')).toBeInTheDocument(),
    );
    await user.click(screen.getByText('dev_agent'));

    await waitFor(() =>
      expect(includeInput).toHaveValue('dev_agent, '),
    );

    // Now the form should be dirty (valid change)
    await waitFor(() =>
      expect(within(content).getByText('Save changes')).toBeInTheDocument(),
    );

    await user.click(within(content).getByText('Save changes'));

    await waitFor(() =>
      expect(within(content).getByText('Saved. Changes will take effect within ~1 minute.', { exact: false })).toBeInTheDocument(),
    );

    expect(savedBody).toBeDefined();
    const body = savedBody as { dreaming: { agents: { include: string[] } } };
    expect(body.dreaming.agents.include).toContain('dev_agent');
    expect(body.dreaming.agents.include).not.toContain('non_existent');
  });

  test('iAC3: valid roster token followed by non-roster trailing token — non-roster is stripped at save', async () => {
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
      expect(within(content).getByText('Included agents')).toBeInTheDocument(),
    );

    const inputs = screen.getAllByPlaceholderText('add agents…');
    const includeInput = inputs[0];
    const user = userEvent.setup();
    await user.click(includeInput);

    // Add a valid roster agent via autocomplete
    await user.type(includeInput, 'dev');
    await waitFor(() =>
      expect(screen.getByRole('listbox')).toBeInTheDocument(),
    );
    await user.click(screen.getByText('dev_agent'));

    await waitFor(() =>
      expect(includeInput).toHaveValue('dev_agent, '),
    );

    // Now type a non-roster name after the comma — this is the trailing
    // (actively-typed) token so RecipientsInput preserves it.
    await user.type(includeInput, 'non_existent');

    // The input should show both tokens
    await waitFor(() =>
      expect(includeInput).toHaveValue('dev_agent, non_existent'),
    );

    // Form should be dirty (valid change: dev_agent added)
    await waitFor(() =>
      expect(within(content).getByText('Save changes')).toBeInTheDocument(),
    );

    await user.click(within(content).getByText('Save changes'));

    await waitFor(() =>
      expect(within(content).getByText('Saved. Changes will take effect within ~1 minute.', { exact: false })).toBeInTheDocument(),
    );

    expect(savedBody).toBeDefined();
    const body = savedBody as { dreaming: { agents: { include: string[] } } };
    // Only the roster-valid token should be in the patch
    expect(body.dreaming.agents.include).toEqual(['dev_agent']);
    expect(body.dreaming.agents.include).not.toContain('non_existent');
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

describe('SettingsPage — Executors section (shared connect flow, THR-107 S2)', () => {
  // The stale THR-052 registration-token generator (name/command/argv/adapter
  // form → legacy /auth/registration-token → static conformance prompt +
  // config.yaml snippet) was replaced by the SAME chrome-free <ConnectFlow>
  // onboarding uses: mode toggle → runtime-token mint → copy-paste prompt →
  // live GET /health/prereqs poll → connected card.
  const RUNTIME_TOKEN = 'hrreg_runtime_token_xyz789';

  function stubMintOk() {
    server.use(
      http.post('/api/v1/auth/registration-token/runtime', () =>
        HttpResponse.json({
          token: RUNTIME_TOKEN,
          expires_at: Math.floor(Date.now() / 1000) + 600,
        }),
      ),
    );
  }

  beforeEach(() => {
    stubBaseHandlers();
    stubMintOk();
    // Default: nothing registered → the poll never matches, flow stays waiting.
    server.use(
      http.get('/api/v1/health/prereqs', () =>
        HttpResponse.json({ prereqs: [] }),
      ),
      // The sibling ExecutorBinariesSection ("CLI binary paths") also mounts on
      // this panel — stub its list so it renders cleanly and its own banner
      // doesn't leak into these connect-flow assertions.
      http.get('/api/v1/executor-binaries', () =>
        HttpResponse.json({ entries: [] }),
      ),
    );
  });

  test('renders the shared connect flow (built-in mode) instead of the stale generator', async () => {
    mountAt(`/orgs/${SLUG}/settings/executors`);

    await waitFor(() =>
      expect(
        screen.getByRole('button', { name: /generate connect prompt/i }),
      ).toBeInTheDocument(),
    );

    // Built-in mode: the agentic-CLI dropdown + the custom-mode toggle.
    expect(screen.getByLabelText('Pick your agentic CLI')).toBeInTheDocument();
    expect(
      screen.getByRole('button', { name: /connect a custom cli instead/i }),
    ).toBeInTheDocument();

    // The orthogonal read-only notice is preserved.
    expect(
      screen.getByText('Per-agent executor assignment'),
    ).toBeInTheDocument();

    // The stale generator + its legacy copy are GONE.
    expect(
      screen.queryByTestId('executor-registration-form'),
    ).not.toBeInTheDocument();
    expect(screen.queryByTestId('conformance-prompt')).not.toBeInTheDocument();
    expect(screen.queryByTestId('config-snippet')).not.toBeInTheDocument();
    expect(screen.queryByText(/happyranch executors register/i)).toBeNull();
    expect(screen.queryByText(/config\.yaml/i)).toBeNull();
    expect(
      screen.queryByRole('button', { name: /generate registration token/i }),
    ).toBeNull();
  });

  test('custom mode: name → runtime-token mint → copy-paste prompt + live poll waiting state', async () => {
    const user = userEvent.setup();
    mountAt(`/orgs/${SLUG}/settings/executors`);

    await waitFor(() =>
      expect(
        screen.getByRole('button', { name: /generate connect prompt/i }),
      ).toBeInTheDocument(),
    );

    await user.click(
      screen.getByRole('button', { name: /connect a custom cli instead/i }),
    );
    await user.type(screen.getByLabelText('Name this CLI'), 'my-cli');
    await user.click(
      screen.getByRole('button', { name: /generate connect prompt/i }),
    );

    // Waiting body: prompt block carries the minted runtime token + the
    // ratified profile register route (NOT the legacy CLI / config.yaml).
    await waitFor(() =>
      expect(screen.getByText(/Waiting for/i)).toBeInTheDocument(),
    );
    const prompt = screen.getByText(/Authorization: Bearer/i);
    expect(prompt.textContent).toContain(RUNTIME_TOKEN);
    expect(prompt.textContent).toContain('/executors/runtime/register');
    expect(prompt.textContent).not.toContain('register-binary');
    expect(prompt.textContent).toContain('workspace_access');
    expect(prompt.textContent).toContain('loopback_reachable');
    expect(prompt.textContent).toContain('cli_callback');
    // No legacy CLI / config.yaml copy anywhere in the flow.
    expect(prompt.textContent).not.toContain('executors register');
    expect(prompt.textContent).not.toContain('config.yaml');
  });

  test('custom mode: the prereqs poll flips to the register-real connected card', async () => {
    // Registered profile appears in prereqs (present stays false for a profile).
    server.use(
      http.get('/api/v1/health/prereqs', () =>
        HttpResponse.json({
          prereqs: [
            { tool: 'my-cli', present: false, path: null, hint: '' },
          ],
        }),
      ),
    );

    const user = userEvent.setup();
    mountAt(`/orgs/${SLUG}/settings/executors`);

    await waitFor(() =>
      expect(
        screen.getByRole('button', { name: /generate connect prompt/i }),
      ).toBeInTheDocument(),
    );

    await user.click(
      screen.getByRole('button', { name: /connect a custom cli instead/i }),
    );
    await user.type(screen.getByLabelText('Name this CLI'), 'my-cli');
    await user.click(
      screen.getByRole('button', { name: /generate connect prompt/i }),
    );

    // Connected card: Settings-context subtitle (no "manage from Settings"
    // clause), register-real name + path, and "Connect another".
    await waitFor(() =>
      expect(
        screen.getByRole('button', { name: /connect another/i }),
      ).toBeInTheDocument(),
    );
    expect(screen.getByText(/available to every org/i)).toBeInTheDocument();
    expect(screen.queryByText(/manage your CLIs anytime from Settings/i)).toBeNull();
    expect(screen.getByText('Registered at')).toBeInTheDocument();
    expect(screen.getByText('on PATH')).toBeInTheDocument();
    expect(screen.getAllByText('my-cli').length).toBeGreaterThan(0);
  });

  test('built-in mode: dropdown → mint → prompt targets register-binary', async () => {
    const user = userEvent.setup();
    mountAt(`/orgs/${SLUG}/settings/executors`);

    const select = await screen.findByLabelText('Pick your agentic CLI');
    // Pick the first real kind (skip the placeholder option).
    const kind = within(select).getAllByRole('option')[1] as HTMLOptionElement;
    await user.selectOptions(select, kind.value);
    await user.click(
      screen.getByRole('button', { name: /generate connect prompt/i }),
    );

    await waitFor(() =>
      expect(screen.getByText(/Waiting for/i)).toBeInTheDocument(),
    );
    const prompt = screen.getByText(/Authorization: Bearer/i);
    expect(prompt.textContent).toContain(RUNTIME_TOKEN);
    expect(prompt.textContent).toContain('/executors/runtime/register-binary');
  });

  test('shows an error when the runtime-token mint fails', async () => {
    // Use 403 (not 401) — a 401 triggers the client's master-bearer retry path.
    server.use(
      http.post('/api/v1/auth/registration-token/runtime', () =>
        HttpResponse.json({ detail: 'not allowed' }, { status: 403 }),
      ),
    );

    const user = userEvent.setup();
    mountAt(`/orgs/${SLUG}/settings/executors`);

    await waitFor(() =>
      expect(
        screen.getByRole('button', { name: /generate connect prompt/i }),
      ).toBeInTheDocument(),
    );

    await user.click(
      screen.getByRole('button', { name: /connect a custom cli instead/i }),
    );
    await user.type(screen.getByLabelText('Name this CLI'), 'bad-cli');
    await user.click(
      screen.getByRole('button', { name: /generate connect prompt/i }),
    );

    // Scope to the connect section so the sibling binaries banner can't match.
    const connect = screen.getByTestId('executors-connect');
    await waitFor(() =>
      expect(within(connect).getByRole('alert')).toHaveTextContent(
        /Could not generate a prompt \(403\)/i,
      ),
    );
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
