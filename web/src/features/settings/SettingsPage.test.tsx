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

describe('SettingsPage — Executors panel (THR-107 S3 registered-list-first management surface)', () => {
  beforeEach(() => {
    stubBaseHandlers();
    server.use(
      // Machine-global RUNTIME mint (NOT the legacy org-scoped route).
      http.post('/api/v1/auth/registration-token/runtime', () =>
        HttpResponse.json({
          token: 'hrreg_runtime_default',
          expires_at: Math.floor(Date.now() / 1000) + 600,
        }),
      ),
      // Nothing registered by default → fresh-env list + the poll stays waiting.
      http.get('/api/v1/executor-binaries', () =>
        HttpResponse.json({ entries: [] }),
      ),
      http.get('/api/v1/health/prereqs', () =>
        HttpResponse.json({ prereqs: [] }),
      ),
    );
  });

  /** Open the inline connect flow via the single "Connect a CLI" entry. */
  async function openConnect(user: ReturnType<typeof userEvent.setup>) {
    await user.click(
      await screen.findByRole('button', { name: /connect a cli/i }),
    );
  }

  test('lands on the registered list + a single "Connect a CLI" entry, not the connect form', async () => {
    mountAt(`/orgs/${SLUG}/settings/executors`);

    // Management-first: the registered binary list is the primary content.
    expect(
      await screen.findByTestId('executor-binaries-section'),
    ).toBeInTheDocument();
    expect(
      screen.getByRole('button', { name: /connect a cli/i }),
    ).toBeInTheDocument();

    // The connect form is gated behind that button — not shown yet.
    expect(screen.queryByTestId('executors-connect')).not.toBeInTheDocument();
    expect(screen.queryByLabelText(/name this cli/i)).not.toBeInTheDocument();

    // The legacy THR-052 surface is gone.
    expect(
      screen.queryByTestId('executor-registration-form'),
    ).not.toBeInTheDocument();
    expect(screen.queryByLabelText('Adapter')).not.toBeInTheDocument();
  });

  test('manual absolute-path entry is DEMOTED behind an "Advanced" disclosure on each row', async () => {
    mountAt(`/orgs/${SLUG}/settings/executors`);

    const row = await screen.findByTestId('binary-row-claude');
    // The disclosure is present; the path input lives under it (kept, not deleted).
    expect(
      within(row).getByText(/advanced: enter path manually/i),
    ).toBeInTheDocument();
    expect(within(row).getByLabelText(/Register binary path/i)).toBeInTheDocument();
  });

  test('Connect a CLI opens the shared flow inline (built-in default), reachable to custom', async () => {
    const user = userEvent.setup();
    mountAt(`/orgs/${SLUG}/settings/executors`);

    await openConnect(user);

    // Built-in mode is the default: the kind dropdown, not the name input.
    expect(await screen.findByLabelText(/pick your agentic cli/i)).toBeInTheDocument();
    expect(screen.getByTestId('executors-connect')).toBeInTheDocument();

    // The mode toggle (built-in convergence) switches to the custom name form.
    await user.click(screen.getByText(/connect a custom cli instead/i));
    expect(await screen.findByLabelText(/name this cli/i)).toBeInTheDocument();
  });

  test('built-in connect mints via the RUNTIME token route and shows the register-binary prompt', async () => {
    const mintPaths: string[] = [];
    server.use(
      http.post('/api/v1/auth/registration-token', () => {
        mintPaths.push('legacy');
        return HttpResponse.json({ token: 'x', expires_at: 0 });
      }),
      http.post('/api/v1/auth/registration-token/runtime', () => {
        mintPaths.push('runtime');
        return HttpResponse.json({
          token: 'hrreg_runtime_bin',
          expires_at: Math.floor(Date.now() / 1000) + 600,
        });
      }),
    );

    const user = userEvent.setup();
    mountAt(`/orgs/${SLUG}/settings/executors`);

    await openConnect(user);
    await user.selectOptions(
      await screen.findByLabelText(/pick your agentic cli/i),
      'claude',
    );
    await user.click(
      screen.getByRole('button', { name: /generate connect prompt/i }),
    );

    const pre = await screen.findByText(/connecting the built-in "claude"/i);
    expect(pre).toHaveTextContent('hrreg_runtime_bin');
    expect(pre).toHaveTextContent('/executors/runtime/register-binary');
    expect(mintPaths).toEqual(['runtime']);
  });

  test('custom connect mints via the RUNTIME token route and shows the profile-register prompt (no legacy CLI / config.yaml)', async () => {
    const mintPaths: string[] = [];
    server.use(
      http.post('/api/v1/auth/registration-token', () => {
        mintPaths.push('legacy');
        return HttpResponse.json({ token: 'x', expires_at: 0 });
      }),
      http.post('/api/v1/auth/registration-token/runtime', () => {
        mintPaths.push('runtime');
        return HttpResponse.json({
          token: 'hrreg_runtime_abc',
          expires_at: Math.floor(Date.now() / 1000) + 600,
        });
      }),
    );

    const user = userEvent.setup();
    mountAt(`/orgs/${SLUG}/settings/executors`);

    await openConnect(user);
    await user.click(screen.getByText(/connect a custom cli instead/i));
    await user.type(await screen.findByLabelText(/name this cli/i), 'my-cli');
    await user.click(
      screen.getByRole('button', { name: /generate connect prompt/i }),
    );

    // The profile copy-paste prompt appears, carrying the runtime token and
    // targeting the profile register route — NOT the legacy CLI or config.yaml.
    const pre = await screen.findByText(/being connected to HappyRanch/i);
    expect(pre).toHaveTextContent('hrreg_runtime_abc');
    expect(pre).toHaveTextContent('/executors/runtime/register');
    expect(pre).not.toHaveTextContent('executors register');
    expect(pre).not.toHaveTextContent('config.yaml');
    expect(pre).not.toHaveTextContent('executor_profiles');

    // Only the runtime route was hit; the legacy org-scoped route was not.
    expect(mintPaths).toEqual(['runtime']);
  });

  test('poll flips to the connected card, then Done collapses back to the refreshed list', async () => {
    server.use(
      http.get('/api/v1/health/prereqs', () =>
        HttpResponse.json({
          prereqs: [{ tool: 'my-cli', present: false, path: '/opt/bin/my-cli', hint: '' }],
        }),
      ),
    );

    const user = userEvent.setup();
    mountAt(`/orgs/${SLUG}/settings/executors`);

    await openConnect(user);
    await user.click(screen.getByText(/connect a custom cli instead/i));
    await user.type(await screen.findByLabelText(/name this cli/i), 'my-cli');
    await user.click(
      screen.getByRole('button', { name: /generate connect prompt/i }),
    );

    expect(
      await screen.findByRole('heading', { name: /my-cli connected/i }),
    ).toBeInTheDocument();
    // Register-real path from prereqs is shown (not fabricated).
    expect(screen.getByText('/opt/bin/my-cli')).toBeInTheDocument();
    // Settings-appropriate subtitle — no circular "manage from Settings" clause.
    expect(screen.queryByText(/manage your CLIs anytime from Settings/i)).not.toBeInTheDocument();

    // Done collapses back to the list (the connect flow unmounts).
    await user.click(screen.getByRole('button', { name: /^done$/i }));
    expect(
      await screen.findByRole('button', { name: /connect a cli/i }),
    ).toBeInTheDocument();
    expect(screen.queryByTestId('executors-connect')).not.toBeInTheDocument();
  });

  test('preserves the name-collision guard against built-ins', async () => {
    const user = userEvent.setup();
    mountAt(`/orgs/${SLUG}/settings/executors`);

    await openConnect(user);
    await user.click(screen.getByText(/connect a custom cli instead/i));
    await user.type(await screen.findByLabelText(/name this cli/i), 'claude');

    expect(screen.getByText(/isn.t a built-in/i)).toBeInTheDocument();
    expect(
      screen.getByRole('button', { name: /generate connect prompt/i }),
    ).toBeDisabled();
  });

  test('keeps the per-agent executor assignment notice verbatim', async () => {
    mountAt(`/orgs/${SLUG}/settings/executors`);

    expect(
      await screen.findByText(/Per-agent executor assignment/i),
    ).toBeInTheDocument();
    expect(screen.getByText('Agents page')).toHaveAttribute('href', '../agents');
  });

  test('no onboarding chrome leaks into Settings (no step eyebrow / Continue / Skip)', async () => {
    const user = userEvent.setup();
    mountAt(`/orgs/${SLUG}/settings/executors`);

    await openConnect(user);
    await screen.findByLabelText(/pick your agentic cli/i);

    expect(screen.queryByText(/step 1 of 2/i)).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /^continue$/i })).not.toBeInTheDocument();
    expect(screen.queryByText(/skip/i)).not.toBeInTheDocument();

    // The built-in↔custom mode toggle is NOT onboarding chrome — it is core to
    // the shared flow (S3 built-in convergence) and SHOULD be present.
    expect(screen.getByText(/connect a custom cli instead/i)).toBeInTheDocument();
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
