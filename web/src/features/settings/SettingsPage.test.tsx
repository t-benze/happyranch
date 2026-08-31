import { screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { http, HttpResponse } from 'msw';
import { describe, expect, test, beforeEach } from 'vitest';
import { useLocation, useNavigationType } from 'react-router-dom';
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
    host_global_session_cap: { value: 13, restart_required: true },
    protocol_dir: { value: 'protocol', restart_required: true },
  },
  org: {
    session_timeout_seconds: null,
    reviewer_agents: ['code_reviewer'],
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
    working_hours: {
      enabled: true,
      agents: { mode: 'all' as const, include: [], exclude: [] },
      default: { mode: 'windowed', window: { start: '09:00', end: '17:00', timezone: 'UTC' }, interval: '2h', days: ['mon','tue','wed','thu','fri'], catch_up_on_startup: false },
      teams: {},
      overrides: {},
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
    http.get(`/api/v1/orgs/${SLUG}/settings/daemon-capacity`, () => HttpResponse.json({
      running_at_daemon_start: { queue_workers: 6, host_global_session_cap: 13 },
      running_provenance: 'startup-resolved settings snapshot',
      persisted_yaml: { queue_workers: null, host_global_session_cap: null },
      next_start: { queue_workers: 6, host_global_session_cap: 13 },
      environment_shadowed: [], environment_warning: null,
      effective_admission_reason: 'Startup-loaded host supervisor policy',
      revision: 'sha256:test', restart_required: false, restart_pending: false,
      guidance: { queue_workers: 'Empirical worker guidance', host_global_session_cap: 'Empirical cap guidance', enforced: false },
      authorization: 'Local operator; daemon bearer required.',
    })),
    http.get(`/api/v1/orgs/${SLUG}/agents`, () =>
      HttpResponse.json(AGENTS_PAYLOAD),
    ),
    http.get('/api/v1/assistant/status', () =>
      HttpResponse.json({ state: 'uninitialized', selected_executor: null, workspace_path: null, detail: null }),
    ),
    http.get(`/api/v1/orgs/${SLUG}/tokens`, () =>
      HttpResponse.json(TOKENS_PAYLOAD),
    ),
    // THR-107 seq339: the adapter-backed connect flow fetches the contract
    // reference with the scoped token after minting.  Return a
    // deterministic non-guessed path so tests can prove the literal
    // server-returned value is rendered through the shared prompt builder.
    http.get('/api/v1/runtime/adapters/contract-reference', () =>
      HttpResponse.json({
        contract_version: 1,
        canonical_adapter_id: 'test-adapter',
        canonical_adapter_id_description: '',
        adapter_input_schema: {},
        adapter_output_schema: {},
        rules: {},
        submission: {},
        dependency_manifest: {},
        token_metering: {},
        reapproval_rule: '',
        probe: {},
        canonical_directory: '/tmp/happyranch-daemon/adapters',
        canonical_directory_description: '',
        required_executable_path: '/tmp/happyranch-daemon/adapters/test-cli-adapter',
        required_executable_path_description: '',
      }),
    ),
  );
}

function mountAt(route: string) {
  sessionStorage.setItem('happyranch.token', 'tok');
  return renderWithProviders(<AppRoutes />, { route });
}

function RouteEvidence(): JSX.Element {
  const location = useLocation();
  const navigationType = useNavigationType();
  return <output data-testid="route-evidence">{navigationType}:{location.pathname}</output>;
}

function mountAtWithRouteEvidence(route: string) {
  sessionStorage.setItem('happyranch.token', 'tok');
  return renderWithProviders(<><AppRoutes /><RouteEvidence /></>, { route });
}

describe('SettingsPage — sub-nav and routing', () => {
  beforeEach(() => {
    stubBaseHandlers();
  });

  test.each([
    `/orgs/${SLUG}/settings`,
    `/orgs/${SLUG}/settings/system`,
    `/orgs/${SLUG}/settings/agents`,
    `/orgs/${SLUG}/settings/unknown`,
  ])('%s resolves to canonical Assistant with replace semantics', async (route) => {
    mountAtWithRouteEvidence(route);

    await waitFor(() => expect(screen.getByText('System Assistant')).toBeInTheDocument());
    expect(screen.getByTestId('route-evidence')).toHaveTextContent(
      `REPLACE:/orgs/${SLUG}/settings/assistant`,
    );
  });

  test('sub-nav renders the dedicated capacity section in canonical order', async () => {
    mountAt(`/orgs/${SLUG}/settings/assistant`);

    await waitFor(() =>
      expect(screen.getByTestId('settings-content')).toBeInTheDocument(),
    );

    const content = screen.getByTestId('settings-content');
    const subnav = within(content).getByRole('complementary');
    expect(within(subnav).getAllByRole('link').map((link) => link.textContent)).toEqual([
      'Daemon / Capacity',
      'Assistant',
      'Organization',
      'Executors',
    ]);
    expect(within(subnav).queryByText('System')).not.toBeInTheDocument();
    expect(within(subnav).queryByText('Agents')).not.toBeInTheDocument();
  });

  test('SET-03: each sub-nav item renders a leading icon', async () => {
    mountAt(`/orgs/${SLUG}/settings/assistant`);

    await waitFor(() =>
      expect(screen.getByTestId('settings-content')).toBeInTheDocument(),
    );

    const content = screen.getByTestId('settings-content');
    const subnav = within(content).getByRole('complementary');

    for (const label of [
      'Daemon / Capacity',
      'Assistant',
      'Organization',
      'Executors',
    ]) {
      const link = within(subnav).getByRole('link', { name: label });
      // Each sub-nav link carries a leading (decorative) icon SVG.
      expect(link.querySelector('svg')).not.toBeNull();
    }
  });

  test('daemon capacity distinguishes running, not-set YAML, next start and no-live-apply copy', async () => {
    mountAt(`/orgs/${SLUG}/settings/daemon-capacity`);
    await screen.findByRole('heading', { name: 'Daemon / Capacity' });
    await screen.findByText('6 workers / cap 13');
    expect(screen.getByRole('alert')).toHaveTextContent(/bearer-based authorization cannot be attributed/);
    expect(screen.getByText('Not set / Not set')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Save for next restart' })).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /restart daemon/i })).not.toBeInTheDocument();
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
    await user.click(within(content).getByText('Organization'));
    await waitFor(() => expect(screen.getByText('Org-level settings.', { exact: false })).toBeInTheDocument());
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
    expect(within(content).queryByText(/reviewer agents/i)).not.toBeInTheDocument();
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

  // ── Operating controls (work-hours enablement + eligibility) ──

  test('renders Operating controls with toggle and eligibility editor', async () => {
    mountAt(`/orgs/${SLUG}/settings/organization`);

    await waitFor(() =>
      expect(screen.getByTestId('settings-content')).toBeInTheDocument(),
    );
    const content = screen.getByTestId('settings-content');

    await waitFor(() =>
      expect(within(content).getByText('Operating controls')).toBeInTheDocument(),
    );

    // Toggle is present in operating controls (not dreaming/threads)
    const operatingControls = within(content).getByTestId('operating-controls');
    const switches = within(operatingControls).getAllByRole('switch');
    expect(switches.length).toBe(1);

    // Eligibility editor button present
    expect(
      within(content).getByRole('button', { name: 'Edit eligibility' }),
    ).toBeInTheDocument();

    // Work Hours deep link present (scoped to link role to avoid the switch label)
    expect(
      within(content).getByRole('link', { name: 'Work Hours' }),
    ).toBeInTheDocument();
  });

  test('toggle disable shows confirmation dialog and saves working_hours-only payload', async () => {
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
      expect(within(content).getByText('Operating controls')).toBeInTheDocument(),
    );

    const user = userEvent.setup();

    // Scope to Operating controls — the Dreaming section also has switches
    const operatingControls = within(content).getByTestId('operating-controls');
    const switches = within(operatingControls).getAllByRole('switch');
    expect(switches.length).toBe(1); // exactly one switch in operating controls
    const whSwitch = switches[0];
    expect(whSwitch.getAttribute('aria-checked')).toBe('true');

    // Click to disable — should show confirmation dialog
    await user.click(whSwitch);
    await waitFor(() => {
      expect(screen.getByText('Disable work hours?')).toBeInTheDocument();
    });

    // Confirm
    await user.click(screen.getByRole('button', { name: 'Disable' }));

    await waitFor(() => {
      expect(screen.queryByText('Disable work hours?')).not.toBeInTheDocument();
    });

    // Verify the PUT payload is working_hours-only
    expect(savedBody).toEqual({
      working_hours: { enabled: false },
    });
  });

  test('toggle enable sends working_hours-only payload immediately', async () => {
    let savedBody: unknown = null;
    // Start with enabled: false
    const disabledPayload = {
      ...SETTINGS_PAYLOAD,
      org: {
        ...SETTINGS_PAYLOAD.org,
        working_hours: {
          ...SETTINGS_PAYLOAD.org.working_hours,
          enabled: false,
        },
      },
    };

    server.use(
      http.get(`/api/v1/orgs/${SLUG}/settings`, () =>
        HttpResponse.json(disabledPayload),
      ),
      http.put(`/api/v1/orgs/${SLUG}/settings/org`, async ({ request }) => {
        savedBody = await request.json();
        return HttpResponse.json(disabledPayload);
      }),
    );

    mountAt(`/orgs/${SLUG}/settings/organization`);

    await waitFor(() =>
      expect(screen.getByTestId('settings-content')).toBeInTheDocument(),
    );
    const content = screen.getByTestId('settings-content');

    await waitFor(() =>
      expect(within(content).getByText('Operating controls')).toBeInTheDocument(),
    );

    const user = userEvent.setup();

    // Scope to Operating controls
    const operatingControls = within(content).getByTestId('operating-controls');
    const whSwitch = within(operatingControls).getByRole('switch');
    expect(whSwitch.getAttribute('aria-checked')).toBe('false');

    await user.click(whSwitch);

    await waitFor(() => {
      expect(screen.getByText(/Saved.*takes effect.*scheduler/)).toBeInTheDocument();
    });

    // Verify working_hours-only payload
    expect(savedBody).toEqual({
      working_hours: { enabled: true },
    });
  });

  test('save error shows inline error in operating controls', async () => {
    server.use(
      http.put(`/api/v1/orgs/${SLUG}/settings/org`, () =>
        HttpResponse.json({ detail: 'Invalid agent reference' }, { status: 422 }),
      ),
    );

    mountAt(`/orgs/${SLUG}/settings/organization`);

    await waitFor(() =>
      expect(screen.getByTestId('settings-content')).toBeInTheDocument(),
    );
    const content = screen.getByTestId('settings-content');

    await waitFor(() =>
      expect(within(content).getByText('Operating controls')).toBeInTheDocument(),
    );

    const user = userEvent.setup();

    // Scope to Operating controls
    const operatingControls = within(content).getByTestId('operating-controls');
    const whSwitch = within(operatingControls).getByRole('switch');
    expect(whSwitch.getAttribute('aria-checked')).toBe('true');

    // Click to disable — confirm dialog shows
    await user.click(whSwitch);
    await waitFor(() => {
      expect(screen.getByText('Disable work hours?')).toBeInTheDocument();
    });
    await user.click(screen.getByRole('button', { name: 'Disable' }));

    await waitFor(() => {
      expect(
        within(content).getByRole('alert'),
      ).toBeInTheDocument();
    });
    expect(
      within(content).getByText(/Invalid agent reference/),
    ).toBeInTheDocument();
  });

  test('Work Hours switch has accessible name and keyboard focus/confirmation flow', async () => {
    server.use(
      http.put(`/api/v1/orgs/${SLUG}/settings/org`, async ({ request }) => {
        const body = await request.json();
        // The keyboard disable flow must send the same working_hours-only shape.
        expect(body).toEqual({ working_hours: { enabled: false } });
        return HttpResponse.json(SETTINGS_PAYLOAD);
      }),
    );

    mountAt(`/orgs/${SLUG}/settings/organization`);

    await waitFor(() =>
      expect(screen.getByTestId('settings-content')).toBeInTheDocument(),
    );
    const content = screen.getByTestId('settings-content');

    await waitFor(() =>
      expect(within(content).getByText('Operating controls')).toBeInTheDocument(),
    );

    const user = userEvent.setup();
    const whSwitch = within(content).getByRole('switch', { name: 'Work Hours' });
    expect(whSwitch.getAttribute('aria-checked')).toBe('true');

    // Focus the switch and activate with keyboard (Enter on a focused button)
    whSwitch.focus();
    expect(document.activeElement).toBe(whSwitch);
    await user.keyboard('{Enter}');

    // Confirmation dialog appears with an accessible title
    await waitFor(() => {
      expect(screen.getByRole('dialog', { name: 'Disable work hours?' })).toBeInTheDocument();
    });
    expect(screen.getByText('Disable work hours?')).toBeInTheDocument();

    // Cancel closes the dialog; focus returns to a sensible control (the switch
    // is reachable again).
    await user.click(screen.getByRole('button', { name: 'Cancel' }));
    await waitFor(() => {
      expect(screen.queryByRole('dialog', { name: 'Disable work hours?' })).not.toBeInTheDocument();
    });

    // Re-open with Space and confirm
    whSwitch.focus();
    await user.keyboard('{Space}');
    await waitFor(() => {
      expect(screen.getByRole('dialog', { name: 'Disable work hours?' })).toBeInTheDocument();
    });
    await user.click(screen.getByRole('button', { name: 'Disable' }));

    // Save feedback is announced via role=status
    await waitFor(() => {
      expect(
        within(content).getByRole('status'),
      ).toHaveTextContent(/Saved.*takes effect.*scheduler/);
    });
  });

  test('Work Hours confirmation restores focus to its switch after Escape and Cancel', async () => {
    mountAt(`/orgs/${SLUG}/settings/organization`);

    await waitFor(() =>
      expect(screen.getByTestId('settings-content')).toBeInTheDocument(),
    );
    const content = screen.getByTestId('settings-content');
    const user = userEvent.setup();
    const whSwitch = within(content).getByRole('switch', { name: 'Work Hours' });

    whSwitch.focus();
    await user.keyboard('{Enter}');
    await waitFor(() =>
      expect(screen.getByRole('dialog', { name: 'Disable work hours?' })).toBeInTheDocument(),
    );
    await user.keyboard('{Escape}');
    await waitFor(() =>
      expect(screen.queryByRole('dialog', { name: 'Disable work hours?' })).not.toBeInTheDocument(),
    );
    expect(document.activeElement).toBe(whSwitch);

    await user.keyboard('{Space}');
    await waitFor(() =>
      expect(screen.getByRole('dialog', { name: 'Disable work hours?' })).toBeInTheDocument(),
    );
    await user.click(screen.getByRole('button', { name: 'Cancel' }));
    await waitFor(() =>
      expect(screen.queryByRole('dialog', { name: 'Disable work hours?' })).not.toBeInTheDocument(),
    );
    expect(document.activeElement).toBe(whSwitch);
  });

  test('eligibility editor opens, previews impact, confirms working_hours.agents-only patch, and recovers from 422', async () => {
    // Start in whitelist mode so the include picker is visible without touching
    // the Radix Select (jsdom lacks PointerEvent#hasPointerCapture).
    const whitelistPayload = {
      ...SETTINGS_PAYLOAD,
      org: {
        ...SETTINGS_PAYLOAD.org,
        working_hours: {
          ...SETTINGS_PAYLOAD.org.working_hours,
          agents: { mode: 'whitelist' as const, include: [], exclude: [] },
        },
      },
    };

    let savedBody: unknown = null;
    let attempt = 0;
    server.use(
      http.get(`/api/v1/orgs/${SLUG}/settings`, () =>
        HttpResponse.json(whitelistPayload),
      ),
      http.put(`/api/v1/orgs/${SLUG}/settings/org`, async ({ request }) => {
        savedBody = await request.json();
        attempt += 1;
        if (attempt === 1) {
          return HttpResponse.json(
            { detail: 'Unknown agent reference: ghost_agent' },
            { status: 422 },
          );
        }
        return HttpResponse.json(whitelistPayload);
      }),
    );

    mountAt(`/orgs/${SLUG}/settings/organization`);

    await waitFor(() =>
      expect(screen.getByTestId('settings-content')).toBeInTheDocument(),
    );
    const content = screen.getByTestId('settings-content');

    await waitFor(() =>
      expect(within(content).getByText('Operating controls')).toBeInTheDocument(),
    );

    const user = userEvent.setup();
    await user.click(within(content).getByRole('button', { name: 'Edit eligibility' }));

    // Dialog opens with accessible title
    await waitFor(() => {
      expect(screen.getByRole('dialog', { name: 'Edit eligibility' })).toBeInTheDocument();
    });

    // Include one agent (scope to the include picker to avoid the exclude duplicate)
    const includeSection = screen.getByText('include').closest('div') as HTMLElement;
    const devChip = within(includeSection).getByRole('button', { name: 'dev_agent' });
    await user.click(devChip);
    expect(devChip.getAttribute('aria-pressed')).toBe('true');

    // Review impact shows resulting eligible set
    await user.click(screen.getByRole('button', { name: 'Review impact…' }));
    await waitFor(() => {
      expect(screen.getByText(/Resulting eligible set:/)).toBeInTheDocument();
    });
    expect(screen.getByText('dev_agent')).toBeInTheDocument();

    // Confirm triggers the save and surfaces 422 without client-side authority
    await user.click(screen.getByRole('button', { name: 'Confirm & save' }));
    await waitFor(() => {
      expect(
        screen.getByRole('alert'),
      ).toHaveTextContent(/Unknown agent reference/);
    });

    // The first attempted payload was the existing working_hours.agents patch only
    expect(savedBody).toEqual({
      working_hours: { agents: { mode: 'whitelist', include: ['dev_agent'], exclude: [] } },
    });

    // Retry: review impact again, then confirm
    await user.click(screen.getByRole('button', { name: 'Review impact…' }));
    await waitFor(() => {
      expect(screen.getByRole('button', { name: 'Confirm & save' })).toBeInTheDocument();
    });
    await user.click(screen.getByRole('button', { name: 'Confirm & save' }));
    await waitFor(() => {
      expect(screen.queryByRole('dialog', { name: 'Edit eligibility' })).not.toBeInTheDocument();
    });
    await waitFor(() => {
      expect(
        within(content).getByRole('status'),
      ).toHaveTextContent(/Saved.*takes effect.*scheduler/);
    });
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
          expires_at: Math.floor(Date.now() / 1000) + 1800,
        }),
      ),
      // Nothing registered by default → fresh-env list + the poll stays waiting.
      http.get('/api/v1/executor-binaries', () =>
        HttpResponse.json({ entries: [] }),
      ),
      http.get('/api/v1/health/prereqs', () =>
        HttpResponse.json({ prereqs: [] }),
      ),
      // Custom-profiles list renders inside the panel now (THR-107 S4b) →
      // must be stubbed (onUnhandledRequest:'error'). Empty by default.
      http.get('/api/v1/executors/runtime/profiles', () =>
        HttpResponse.json({ profiles: [] }),
      ),
      // Adapter list is consumed by PendingAdaptersSection + CustomProfilesSection.
      http.get('/api/v1/runtime/adapters', () => HttpResponse.json([])),
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

  test('seq334: ordinary Executors settings shows one Custom CLIs surface and no standalone Custom Adapters list or adapter terminology', async () => {
    mountAt(`/orgs/${SLUG}/settings/executors`);
    await screen.findByTestId('executor-binaries-section');

    // Only the unified Custom CLIs area — no separate Custom Adapters section.
    expect(screen.queryByText('Custom Adapters')).not.toBeInTheDocument();
    expect(screen.queryByTestId('adapter-management-section')).not.toBeInTheDocument();
    expect(screen.queryByTestId('adapter-rows')).not.toBeInTheDocument();

    // Adapter implementation details (id, eligibility, command/workspace adapter)
    // must not surface in ordinary founder-facing Settings.
    expect(screen.queryByText(/Eligibility:/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/Command adapter:/i)).not.toBeInTheDocument();
    // The Custom CLIs heading is present.
    expect(screen.getByText('Custom CLIs')).toBeInTheDocument();
  });

  test('THR-107 slice 3: direct connect lands as one Custom CLI row, no approval step', async () => {
    const profileName = 'direct-custom-cli';
    const executable = '/tmp/happyranch-daemon/adapters/direct-custom-cli-adapter';
    const operationId = 'op-direct-334';

    const boundProfile = {
      name: profileName,
      command: null,
      command_adapter_id: `custom-adapter:${profileName}-adapter`,
      workspace_adapter_id: 'codex',
      adapter: null,
      adapter_id: null,
      command_adapter: null,
      present: true,
      path: executable,
      envelope_policy: 'strict',
    };

    let connected = false;
    let commitCalls = 0;

    server.use(
      http.post('/api/v1/auth/registration-token/runtime', () =>
        HttpResponse.json({ token: 'hrreg_direct_334', expires_at: Math.floor(Date.now() / 1000) + 1800 }),
      ),
      http.get('/api/v1/runtime/custom-cli/status', () =>
        HttpResponse.json({
          wrapper_destination: executable,
          operation_id: connected ? operationId : null,
          profile_state: connected ? 'committed' : null,
          reason: null,
          state: connected ? 'connected' : null,
          retry_eligible: false,
        }),
      ),
      http.post(`/api/v1/runtime/custom-cli/${operationId}/commit`, () => {
        commitCalls += 1;
        return HttpResponse.json({ operation_id: operationId, profile_state: 'committed', profile_name: profileName });
      }),
      http.get('/api/v1/executors/runtime/profiles', () =>
        HttpResponse.json({ profiles: connected ? [boundProfile] : [] }),
      ),
    );

    const user = userEvent.setup();
    mountAt(`/orgs/${SLUG}/settings/executors`);

    await openConnect(user);
    await user.click(await screen.findByRole('button', { name: /connect a custom cli instead/i }));
    expect(await screen.findByText(/create a custom adapter wrapper/i)).toBeInTheDocument();

    await user.type(await screen.findByLabelText(/name this cli/i), profileName);
    await user.click(screen.getByRole('button', { name: /generate connect prompt/i }));

    // Waiting — no approval wording anywhere in the flow.
    await screen.findByLabelText(/waiting for adapter submission/i);
    expect(screen.queryByText(/awaiting approval/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/PENDING/)).not.toBeInTheDocument();

    // Simulate the candidate CLI's own POST /connect landing.
    connected = true;

    await screen.findByRole('heading', { name: new RegExp(profileName, 'i') }, { timeout: 10000 });
    expect(commitCalls).toBe(0);

    // Done collapses back to the list → the newly connected CLI appears.
    await user.click(screen.getByRole('button', { name: /^done$/i }));
    await waitFor(() => {
      expect(screen.getByTestId(`profile-row-${profileName}`)).toBeInTheDocument();
    });
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
    // Default custom path is now adapter-backed; click through to legacy
    await user.click(screen.getByText(/use legacy simple integration instead/i));
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
          expires_at: Math.floor(Date.now() / 1000) + 1800,
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

    const pre = await screen.findByText(/connect the built-in.*claude/i);
    expect(pre).toHaveTextContent('hrreg_runtime_bin');
    expect(pre).toHaveTextContent('/executors/runtime/register-binary');
    expect(mintPaths).toEqual(['runtime']);
    // THR-107 seq352: prompt parity — same structure as onboarding
    const text = pre.textContent || '';
    expect(text).toMatch(/--fail-with-body/);
    expect(text).toMatch(/all_complete/);
    const regIdx = text.indexOf('register-binary');
    const acIdx = text.indexOf('all_complete');
    expect(acIdx).toBeLessThan(regIdx);
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
          expires_at: Math.floor(Date.now() / 1000) + 1800,
        });
      }),
    );

    const user = userEvent.setup();
    mountAt(`/orgs/${SLUG}/settings/executors`);

    await openConnect(user);
    await user.click(screen.getByText(/connect a custom cli instead/i));
    // Default custom path is now adapter-backed; click through to legacy
    await user.click(screen.getByText(/use legacy simple integration instead/i));
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

    // The generated prompt contains the command/argv_template[0] parity JSON.
    // The register step line is:
    // #    body {"command":"<your-cli>","argv_template":["<your-cli>","--flag","{prompt}"],"adapter":"pi"}
    const promptText = pre.textContent ?? '';
    // Extract the register request JSON, parse it, and directly assert
    // command === argv_template[0] (same executable, the parity invariant).
    const bodyMatch = promptText.match(/#\s+body\s+(\{.*"argv_template".*\})/);
    expect(bodyMatch).not.toBeNull();
    const body = JSON.parse(bodyMatch![1]);
    expect(body.command).toBe(body.argv_template[0]);
    // argv_template is the complete invocation (retains placeholder + full args).
    expect(body.argv_template.join(' ')).toContain('{prompt}');
    expect(body).toHaveProperty('adapter');
  });

  test('poll flips to the connected card, then Done collapses back to the refreshed list', async () => {
    server.use(
      http.get('/api/v1/health/prereqs', () =>
        HttpResponse.json({
          prereqs: [{ tool: 'my-cli', present: true, path: '/opt/bin/my-cli', hint: '' }],
        }),
      ),
    );

    const user = userEvent.setup();
    mountAt(`/orgs/${SLUG}/settings/executors`);

    await openConnect(user);
    await user.click(screen.getByText(/connect a custom cli instead/i));
    // Default custom path is now adapter-backed; click through to legacy
    await user.click(screen.getByText(/use legacy simple integration instead/i));
    await user.type(await screen.findByLabelText(/name this cli/i), 'my-cli');
    await user.click(
      screen.getByRole('button', { name: /generate connect prompt/i }),
    );

    expect(
      await screen.findByRole('heading', { name: /my-cli connected/i }),
    ).toBeInTheDocument();
    // Register-real path from prereqs is shown (not fabricated);
    // connected card consumes the present + path from server truth.
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

  test('Done after a custom connect refetches the profiles list so the just-connected CLI appears (invalidation, not stale cache)', async () => {
    // A custom connect creates a profile the panel must show on return. The
    // profiles list starts EMPTY (cached at first mount), then — as the connect
    // completes — the machine-global store gains the new profile. That query
    // carries a 10s staleTime, so CustomProfilesSection remounts on Done INSIDE
    // the stale window: only the explicit invalidation forces the refetch that
    // surfaces the just-connected CLI. Without it the row stays invisible.
    let profileGets = 0;
    let store: {
      name: string;
      command: string | null;
      adapter: string | null;
      present: boolean;
      path: string | null;
    }[] = [];
    server.use(
      http.get('/api/v1/health/prereqs', () =>
        HttpResponse.json({
          prereqs: [{ tool: 'my-cli', present: true, path: '/opt/bin/my-cli', hint: '' }],
        }),
      ),
      http.get('/api/v1/executors/runtime/profiles', () => {
        profileGets += 1;
        return HttpResponse.json({ profiles: store });
      }),
    );

    const user = userEvent.setup();
    mountAt(`/orgs/${SLUG}/settings/executors`);

    // Initial mount: the list loads EMPTY and is cached under the
    // runtime-profiles key (staleTime 10s).
    expect(await screen.findByTestId('custom-profiles-empty')).toBeInTheDocument();
    const getsBeforeConnect = profileGets;

    // Drive a custom connect to the connected card.
    await openConnect(user);
    await user.click(screen.getByText(/connect a custom cli instead/i));
    // Default custom path is now adapter-backed; click through to legacy
    await user.click(screen.getByText(/use legacy simple integration instead/i));
    await user.type(await screen.findByLabelText(/name this cli/i), 'my-cli');
    await user.click(
      screen.getByRole('button', { name: /generate connect prompt/i }),
    );
    expect(
      await screen.findByRole('heading', { name: /my-cli connected/i }),
    ).toBeInTheDocument();

    // The CLI registered during the connect → the store now holds the profile.
    store = [
      { name: 'my-cli', command: 'my-cli', adapter: 'pi', present: true, path: '/opt/bin/my-cli' },
    ];

    // Done collapses back to the list → invalidates the profiles query → refetch.
    await user.click(screen.getByRole('button', { name: /^done$/i }));

    // The just-connected custom CLI now renders (the forced refetch surfaced it)…
    expect(await screen.findByTestId('profile-row-my-cli')).toBeInTheDocument();
    // …and it took a fresh GET to do so — the stale cache did not silently
    // satisfy the remount (this is the assertion that fails on the old
    // executor-binaries-only invalidation).
    expect(profileGets).toBeGreaterThan(getsBeforeConnect);
  });

  test('preserves the name-collision guard against built-ins', async () => {
    const user = userEvent.setup();
    mountAt(`/orgs/${SLUG}/settings/executors`);

    await openConnect(user);
    await user.click(screen.getByText(/connect a custom cli instead/i));
    // Default custom path is now adapter-backed; click through to legacy
    await user.click(screen.getByText(/use legacy simple integration instead/i));
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

  test('THR-107 slice 3: ordinary Executors surface shows Custom CLIs with no pending-approval or adapter terminology', async () => {
    server.use(
      http.get('/api/v1/executors/runtime/profiles', () =>
        HttpResponse.json({
          profiles: [
            {
              name: 'my-custom-cli',
              command: 'my-custom-cli',
              adapter: null,
              workspace_adapter_id: null,
              command_adapter_id: null,
              adapter_id: null,
              command_adapter: null,
              present: true,
              path: '/usr/local/bin/my-custom-cli',
              envelope_policy: null,
            },
          ],
        }),
      ),
    );

    mountAt(`/orgs/${SLUG}/settings/executors`);

    await screen.findByTestId('profile-row-my-custom-cli');

    const bodyText = document.body.textContent ?? '';
    expect(bodyText).toMatch(/Custom CLIs/i);
    // No pending-approval surface — direct-connect has no approval step.
    expect(bodyText).not.toMatch(/pending/i);
    expect(bodyText).not.toMatch(/approval/i);
    // No ordinary founder-visible /adapter/i wording remains.
    expect(bodyText).not.toMatch(/adapter/i);
  });

  test('THR-107 slice 3: direct-connect prompt has no approval wording and includes the daemon-issued wrapper path', async () => {
    server.use(
      http.post('/api/v1/auth/registration-token/runtime', () =>
        HttpResponse.json({
          token: 'hrreg_settings_prompt',
          expires_at: Math.floor(Date.now() / 1000) + 1800,
        }),
      ),
      http.get('/api/v1/runtime/custom-cli/status', () =>
        HttpResponse.json({
          wrapper_destination: '/tmp/happyranch-daemon/adapters/prompt-test-adapter',
          operation_id: null,
          profile_state: null,
          reason: null,
          state: null,
          retry_eligible: false,
        }),
      ),
    );
    const user = userEvent.setup();
    mountAt(`/orgs/${SLUG}/settings/executors`);
    await openConnect(user);
    await user.click(await screen.findByRole('button', { name: /connect a custom cli instead/i }));
    expect(await screen.findByText(/create a custom adapter wrapper/i)).toBeInTheDocument();

    await user.type(await screen.findByLabelText(/name this cli/i), 'prompt-test');
    await user.click(screen.getByRole('button', { name: /generate connect prompt/i }));

    await screen.findByLabelText(/waiting for adapter submission/i);
    const promptText = document.querySelector('pre')?.textContent || '';

    // The literal daemon-issued wrapper path appears, not a placeholder.
    expect(promptText).toContain('/tmp/happyranch-daemon/adapters/prompt-test-adapter');
    // Exact I/O contract carried over from the legacy prompt.
    expect(promptText).toContain('exactly one v1 AdapterInput JSON object from stdin');
    expect(promptText).toContain('exactly one v1 AdapterOutput JSON object to stdout');
    expect(promptText).toContain('Forward the ENTIRE AdapterInput.prompt');
    expect(promptText).toContain('fresh opaque canary');
    expect(promptText).toContain('never emit success without a real provider');
    expect(promptText).toContain('receipt-only call');
    expect(promptText).toContain('starts no subprocess');
    expect(promptText).not.toContain('CodeBuddy');
    // Dependency declaration + never-PATH wording carried over.
    expect(promptText).toContain('never selects an agentic CLI via');
    // Explicitly says there is no approval wait, and never mentions PENDING.
    expect(promptText).not.toContain('PENDING');
    expect(promptText).toContain('no approval step');
    // Single POST connects — no separate submit/bind step.
    expect(promptText).toContain('/runtime/custom-cli/connect');
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
