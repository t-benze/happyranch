import { describe, expect, test, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, act } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { http, HttpResponse } from 'msw';
import { DataContext } from '@/design-system/providers/DataContext';
import { I18nTestBoundary, renderWithProviders } from '@/test/render';
import { server } from '@/test/server';
import { SettingsDialog } from './SettingsDialog';
import { SettingsPage } from './SettingsPage';
import type { SettingsSnapshot, SystemSettings, OrgSettings, OrgSettingsPatch } from '@/lib/api/types';
import type { QueryLike, MutationLike } from '@/design-system/providers/DataContext';

const mockSystem: SystemSettings = {
  claude_cli_path: { value: '/usr/local/bin/claude', restart_required: true },
  codex_cli_path: { value: '/usr/local/bin/codex', restart_required: true },
  opencode_cli_path: { value: '/usr/local/bin/opencode', restart_required: true },
  pi_cli_path: { value: '/usr/local/bin/pi', restart_required: true },
  session_timeout_seconds: { value: 1800, restart_required: false },
  queue_workers: { value: 3, restart_required: true },
  host_global_session_cap: { value: 13, restart_required: true },
  protocol_dir: { value: 'protocol', restart_required: true },
};

const mockOrg: OrgSettings = {
  session_timeout_seconds: 3600,
  reviewer_agents: ['code_reviewer'],
  dreaming: {
    enabled: true,
    schedule: { time: '02:00', timezone: 'UTC' },
    catch_up_on_startup: true,
    agents: {
      mode: 'all',
      include: [],
      exclude: ['qa_engineer'],
    },
  },
  threads: {
    enabled: true,
    default_turn_cap: 500,
    invocation_timeout_seconds: null,
  },
  working_hours: {
    enabled: true,
    agents: { mode: 'all', include: [], exclude: [] },
    default: {
      mode: 'windowed',
      window: { start: '09:00', end: '17:00', timezone: 'UTC' },
      interval: '2h',
      days: ['mon', 'tue', 'wed', 'thu', 'fri'],
      catch_up_on_startup: false,
    },
    teams: {},
    overrides: {},
  },
};

const mockSnapshot: SettingsSnapshot = {
  system: mockSystem,
  org: mockOrg,
};

function renderDialog(
  overrides?: Partial<SettingsSnapshot>,
  onClose = vi.fn(),
  mutateAsync = vi.fn().mockResolvedValue(mockSnapshot),
  queryOverride?: Partial<QueryLike<SettingsSnapshot>>,
  page = false,
) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const snapshot = overrides
    ? { ...mockSnapshot, ...overrides }
    : mockSnapshot;

  const useSettings = (): QueryLike<SettingsSnapshot> => ({
    data: snapshot,
    isLoading: false,
    isError: false,
    error: null,
    ...queryOverride,
  });

  const useUpdateOrgSettings = (): MutationLike<OrgSettingsPatch, SettingsSnapshot> => ({
    mutateAsync,
    isPending: false,
  });

  const ctxValue = {
    settings: { useSettings, useUpdateOrgSettings },
  } as unknown as Parameters<typeof DataContext.Provider>[0]['value'];

  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={[page ? '/orgs/alpha/settings/daemon-capacity' : '/orgs/alpha/dashboard']}>
        <Routes>
          <Route
            element={
              <DataContext.Provider value={ctxValue}>
                {page ? (
                  <I18nTestBoundary>
                    <SettingsPage />
                  </I18nTestBoundary>
                ) : (
                  <SettingsDialog open onOpenChange={onClose} />
                )}
              </DataContext.Provider>
            }
          >
            <Route path={page ? "/orgs/:slug/settings/*" : "/orgs/:slug/dashboard"} element={<div />} />
          </Route>
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  vi.restoreAllMocks();
});

afterEach(() => {
  vi.useRealTimers();
});

describe('SettingsDialog', () => {
  test('renders System and Org sections with all fields', async () => {
    renderDialog();

    expect(screen.getByText('Settings')).toBeInTheDocument();

    // System section
    expect(screen.getByText('System')).toBeInTheDocument();
    expect(screen.getByText(/Claude CLI path/)).toBeInTheDocument();
    expect(screen.getByText(/\/usr\/local\/bin\/claude/)).toBeInTheDocument();
    expect(screen.getByText('1800')).toBeInTheDocument();

    // Org section — editable form
    expect(screen.getByText('Org')).toBeInTheDocument();
    expect(screen.getByRole('heading', { name: 'Dreaming' })).toBeInTheDocument();
    expect(screen.getByRole('heading', { name: 'Threads' })).toBeInTheDocument();

    // Save button exists
    expect(screen.getByRole('button', { name: 'Save' })).toBeInTheDocument();

    // Input fields exist for editable values (text + number inputs)
    const textInputs = screen.getAllByRole('textbox');
    const numberInputs = screen.getAllByRole('spinbutton');
    expect(textInputs.length + numberInputs.length).toBeGreaterThanOrEqual(6);
  });

  test('shows restart-required badges for CLI paths and orchestration fields', async () => {
    renderDialog();

    const badges = screen.getAllByText('Restart required');
    expect(badges.length).toBe(6); // 4 CLI paths + queue_workers + protocol_dir

    // Session timeout should NOT have a restart badge
    const sessionRows = screen.getAllByText('Session timeout (s)');
    for (const row of sessionRows) {
      const parentRow = row.closest('div.flex.items-center');
      expect(parentRow).not.toBeNull();
      expect(parentRow?.querySelector('span.bg-bg-raised')).toBeNull();
    }
  });

  test('shows loading state when query is loading', () => {
    const onClose = vi.fn();
    const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });

    const useSettings = (): QueryLike<SettingsSnapshot> => ({
      data: undefined,
      isLoading: true,
      isError: false,
      error: null,
    });
    const useUpdateOrgSettings = (): MutationLike<OrgSettingsPatch, SettingsSnapshot> => ({
      mutateAsync: vi.fn(),
      isPending: false,
    });

    render(
      <QueryClientProvider client={qc}>
        <MemoryRouter initialEntries={['/orgs/alpha/dashboard']}>
          <Routes>
            <Route
              path="/orgs/:slug/dashboard"
              element={
                <DataContext.Provider
                  value={
                    {
                      settings: { useSettings, useUpdateOrgSettings },
                    } as unknown as Parameters<typeof DataContext.Provider>[0]['value']
                  }
                >
                  <SettingsDialog open onOpenChange={onClose} />
                </DataContext.Provider>
              }
            />
          </Routes>
        </MemoryRouter>
      </QueryClientProvider>,
    );

    expect(screen.getByText(/Loading settings/)).toBeInTheDocument();
  });

  test('shows error state when query fails', () => {
    const onClose = vi.fn();
    const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });

    const useSettings = (): QueryLike<SettingsSnapshot> => ({
      data: undefined,
      isLoading: false,
      isError: true,
      error: new Error('Connection refused'),
    });
    const useUpdateOrgSettings = (): MutationLike<OrgSettingsPatch, SettingsSnapshot> => ({
      mutateAsync: vi.fn(),
      isPending: false,
    });

    render(
      <QueryClientProvider client={qc}>
        <MemoryRouter initialEntries={['/orgs/alpha/dashboard']}>
          <Routes>
            <Route
              path="/orgs/:slug/dashboard"
              element={
                <DataContext.Provider
                  value={
                    {
                      settings: { useSettings, useUpdateOrgSettings },
                    } as unknown as Parameters<typeof DataContext.Provider>[0]['value']
                  }
                >
                  <SettingsDialog open onOpenChange={onClose} />
                </DataContext.Provider>
              }
            />
          </Routes>
        </MemoryRouter>
      </QueryClientProvider>,
    );

    expect(screen.getByText(/Could not load settings/)).toBeInTheDocument();
  });

  test('shows editable form inputs with correct initial values', () => {
    renderDialog();

    // Session timeout input should show 3600
    const timeoutInput = screen.getAllByDisplayValue('3600');
    expect(timeoutInput.length).toBe(1);

    // Dreaming time input should show 02:00
    const timeInput = screen.getByDisplayValue('02:00');
    expect(timeInput).toBeInTheDocument();

    // Default turn cap must NOT be rendered (THR-046 msg126)
    expect(screen.queryByText('Default turn cap')).not.toBeInTheDocument();

    // Excluded agents should show qa_engineer
    const excludeInput = screen.getByDisplayValue('qa_engineer');
    expect(excludeInput).toBeInTheDocument();
  });

  test('no feishu or agent references appear in the dialog', () => {
    renderDialog();

    const html = document.body.innerHTML;
    expect(html).not.toContain('feishu');
    expect(html).not.toContain('Feishu');
    // The dreaming section has "Agent mode" so we can't just search for "agent"
    // But there should be no standalone "Agents" section
    expect(screen.queryByRole('heading', { name: 'Agents' })).toBeNull();
    // Also check there's no feishu anywhere
    expect(html).not.toMatch(/feishu/i);
  });

  test('sends explicit null when session timeout field is cleared', async () => {
    const mutateAsync = vi.fn().mockResolvedValue(mockSnapshot);
    renderDialog(undefined, vi.fn(), mutateAsync);

    // Find the session timeout input (has initial value "3600")
    const timeoutInput = screen.getByDisplayValue('3600');

    // Clear the field
    fireEvent.change(timeoutInput, { target: { value: '' } });

    // Click Save
    const saveButton = screen.getByRole('button', { name: 'Save' });
    fireEvent.click(saveButton);

    // Assert mutation was called with session_timeout_seconds: null (not undefined)
    await vi.waitFor(() => {
      expect(mutateAsync).toHaveBeenCalledTimes(1);
    });
    const patch = mutateAsync.mock.calls[0][0] as OrgSettingsPatch;
    expect(patch.session_timeout_seconds).toBeNull();
  });

  test('sends null for invocation_timeout_seconds when cleared', async () => {
    const mutateAsync = vi.fn().mockResolvedValue(mockSnapshot);
    renderDialog(undefined, vi.fn(), mutateAsync);

    // The invocation timeout input has the label
    const label = screen.getByText(/Invocation timeout/);
    expect(label).toBeInTheDocument();

    // Click Save without changing anything — threads.invocation_timeout_seconds
    // is currently null in mockOrg, so the clear patch should send explicit null
    const saveButton = screen.getByRole('button', { name: 'Save' });
    fireEvent.click(saveButton);

    await vi.waitFor(() => {
      expect(mutateAsync).toHaveBeenCalledTimes(1);
    });
    const patch = mutateAsync.mock.calls[0][0] as OrgSettingsPatch;
    // If invocation_timeout_seconds is null, it should be sent as null
    // (not undefined which would be stripped). Since the input is empty,
    // it translates to null which should be sent as null.
    expect(patch.threads?.invocation_timeout_seconds).toBeNull();
  });

});

// THR-294 — retained dialog makes no assistant status request
// =============================================================================

const SETTINGS_FIXTURE: SettingsSnapshot = {
  system: {
    claude_cli_path: { value: '/usr/local/bin/claude', restart_required: true },
    codex_cli_path: { value: '/usr/local/bin/codex', restart_required: true },
    opencode_cli_path: { value: '/usr/local/bin/opencode', restart_required: true },
    pi_cli_path: { value: '/usr/local/bin/pi', restart_required: true },
    session_timeout_seconds: { value: 1800, restart_required: true },
    queue_workers: { value: 3, restart_required: true },
    host_global_session_cap: { value: 13, restart_required: true },
    protocol_dir: { value: 'protocol', restart_required: true },
  },
  org: {
    session_timeout_seconds: 3600,
    reviewer_agents: ['code_reviewer'],
    dreaming: {
      enabled: true,
      schedule: { time: '02:00', timezone: 'UTC' },
      catch_up_on_startup: true,
      agents: { mode: 'all', include: [], exclude: [] },
    },
    threads: {
      enabled: true,
      default_turn_cap: 500,
      invocation_timeout_seconds: null,
    },
    working_hours: {
      enabled: true,
      agents: { mode: 'all', include: [], exclude: [] },
      default: {
        mode: 'windowed',
        window: { start: '09:00', end: '17:00', timezone: 'UTC' },
        interval: '2h',
        days: ['mon', 'tue', 'wed', 'thu', 'fri'],
        catch_up_on_startup: false,
      },
      teams: {},
      overrides: {},
    },
  },
};

function stubSettings(snapshot?: SettingsSnapshot) {
  server.use(
    http.get('/api/v1/orgs/alpha/settings', () =>
      HttpResponse.json(snapshot ?? SETTINGS_FIXTURE),
    ),
  );
}

function countingAssistantStub(): { count: () => number } {
  let count = 0;
  server.use(
    http.get('/api/v1/assistant/status', () => {
      count += 1;
      return HttpResponse.json({
        state: 'configured',
        selected_executor: 'claude',
        workspace_path: '/rt/system/assistant/workspace',
        detail: null,
      });
    }),
  );
  return { count: () => count };
}

describe('SettingsDialog — assistant status network evidence', () => {
  beforeEach(() => {
    stubSettings();
  });

  test('closed: zero assistant status requests', async () => {
    const counter = countingAssistantStub();
    sessionStorage.setItem('happyranch.token', 'tok');

    // Install fake timers BEFORE mounting.
    vi.useFakeTimers();
    renderWithProviders(
      <Routes>
        <Route
          path="/orgs/:slug/dashboard"
          element={<SettingsDialog open={false} onOpenChange={vi.fn()} />}
        />
      </Routes>,
      { route: '/orgs/alpha/dashboard' },
    );

    // Advance past the old 5 000 ms refetchInterval.
    await act(() => vi.advanceTimersByTimeAsync(6_000));
    expect(counter.count()).toBe(0);
  });

  test('open: zero assistant status requests', async () => {
    const counter = countingAssistantStub();
    sessionStorage.setItem('happyranch.token', 'tok');

    renderWithProviders(
      <Routes>
        <Route
          path="/orgs/:slug/dashboard"
          element={<SettingsDialog open={true} onOpenChange={vi.fn()} />}
        />
      </Routes>,
      { route: '/orgs/alpha/dashboard' },
    );

    // Opening the retained dialog renders ordinary fields without feature requests.
    await vi.waitFor(() => expect(counter.count()).toBe(0));
  });

  test('open: no interval requests', async () => {
    const counter = countingAssistantStub();
    sessionStorage.setItem('happyranch.token', 'tok');

    // Install fake timers BEFORE mounting.
    vi.useFakeTimers();
    renderWithProviders(
      <Routes>
        <Route
          path="/orgs/:slug/dashboard"
          element={<SettingsDialog open={true} onOpenChange={vi.fn()} />}
        />
      </Routes>,
      { route: '/orgs/alpha/dashboard' },
    );

    // Flush ordinary settings reads and React re-render.
    await act(() => vi.advanceTimersByTimeAsync(200));
    expect(counter.count()).toBe(0);

    // Advance past the old 5 000 ms refetchInterval.
    await act(() => vi.advanceTimersByTimeAsync(6_000));
    expect(counter.count()).toBe(0);
  });
});


describe('active settings surfaces — retired maximum absence', () => {
  test.each([false, true])('loading surface page=%s has no maximum', (page) => {
    renderDialog(undefined, vi.fn(), vi.fn(),
      { data: undefined, isLoading: true }, page);
    expect(screen.getByText('Loading settings…')).toBeInTheDocument();
    expect(screen.queryByText(/max(?:imum)? orchestration steps|step budget/i)).not.toBeInTheDocument();
  });
  test.each([false, true])('error surface page=%s has no maximum', (page) => {
    renderDialog(undefined, vi.fn(), vi.fn(),
      { data: undefined, isError: true, error: new Error('synthetic failure') }, page);
    expect(screen.getByText(/Could not load settings/)).toBeInTheDocument();
    expect(screen.queryByText(/max(?:imum)? orchestration steps|step budget/i)).not.toBeInTheDocument();
  });
  test.each([false, true])('synthetic no-data page=%s is a shell, not API-empty success', (page) => {
    renderDialog(undefined, vi.fn(), vi.fn(),
      { data: undefined }, page);
    expect(screen.getByText('Settings')).toBeInTheDocument();
    expect(screen.queryByText('Loading settings…')).not.toBeInTheDocument();
    expect(screen.queryByText(/Could not load settings/)).not.toBeInTheDocument();
    expect(screen.queryByTestId('settings-content')).not.toBeInTheDocument();
    expect(screen.queryByText('Queue workers')).not.toBeInTheDocument();
    expect(screen.queryByText(/max(?:imum)? orchestration steps|step budget/i)).not.toBeInTheDocument();
  });
  test('populated dialog keeps real fields while omitting maximum', () => {
    renderDialog();
    expect(screen.getByText('Queue workers')).toBeInTheDocument();
    expect(screen.getAllByText('Session timeout (s)')).toHaveLength(2);
    expect(screen.queryByText(/max(?:imum)? orchestration steps|step budget/i)).not.toBeInTheDocument();
  });
  test('empty optional org values retain a usable dialog form and no maximum', () => {
    renderDialog({ org: { ...mockOrg, session_timeout_seconds: null,
      reviewer_agents: [], dreaming: { ...mockOrg.dreaming,
        agents: { mode: 'all', include: [], exclude: [] } } } });
    expect(screen.getByText('Queue workers')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /save/i })).toBeInTheDocument();
    expect(screen.queryByText(/max(?:imum)? orchestration steps|step budget/i)).not.toBeInTheDocument();
  });
});
