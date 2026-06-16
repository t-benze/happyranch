import { describe, expect, test, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { DataContext } from '@/design-system/providers/DataContext';
import { SettingsDialog } from './SettingsDialog';
import type { SettingsSnapshot, SystemSettings, OrgSettings, OrgSettingsPatch, AssistantStatus, AssistantRegisterBody } from '@/lib/api/types';
import type { QueryLike, MutationLike } from '@/design-system/providers/DataContext';

const mockSystem: SystemSettings = {
  claude_cli_path: { value: '/usr/local/bin/claude', restart_required: true },
  codex_cli_path: { value: '/usr/local/bin/codex', restart_required: true },
  opencode_cli_path: { value: '/usr/local/bin/opencode', restart_required: true },
  pi_cli_path: { value: '/usr/local/bin/pi', restart_required: true },
  session_timeout_seconds: { value: 1800, restart_required: false },
  max_orchestration_steps: { value: 50, restart_required: true },
  queue_workers: { value: 3, restart_required: true },
  protocol_dir: { value: 'protocol', restart_required: true },
};

const mockOrg: OrgSettings = {
  session_timeout_seconds: 3600,
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
};

const mockSnapshot: SettingsSnapshot = {
  system: mockSystem,
  org: mockOrg,
};

function renderDialog(
  overrides?: Partial<SettingsSnapshot>,
  onClose = vi.fn(),
  mutateAsync = vi.fn().mockResolvedValue(mockSnapshot),
  assistantOverrides?: {
    status?: Partial<AssistantStatus>;
    initMutateAsync?: ReturnType<typeof vi.fn>;
    repairMutateAsync?: ReturnType<typeof vi.fn>;
    registerMutateAsync?: ReturnType<typeof vi.fn>;
  },
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
  });

  const useUpdateOrgSettings = (): MutationLike<OrgSettingsPatch, SettingsSnapshot> => ({
    mutateAsync,
    isPending: false,
  });

  // -- assistant mocks
  const assistantStatus: AssistantStatus = {
    state: 'configured',
    selected_executor: 'claude',
    workspace_path: '/rt/system/assistant/workspace',
    detail: null,
    ...assistantOverrides?.status,
  };

  const useAssistantStatus = (): QueryLike<AssistantStatus> => ({
    data: assistantStatus,
    isLoading: false,
    isError: false,
    error: null,
  });

  const initMutateAsync =
    assistantOverrides?.initMutateAsync ??
    vi.fn().mockResolvedValue(assistantStatus);
  const useInitAssistant = (): MutationLike<{ reconfigure: boolean }, AssistantStatus> => ({
    mutateAsync: initMutateAsync,
    isPending: false,
  });

  const repairMutateAsync =
    assistantOverrides?.repairMutateAsync ??
    vi.fn().mockResolvedValue(assistantStatus);
  const useRepairAssistant = (): MutationLike<void, AssistantStatus> => ({
    mutateAsync: repairMutateAsync,
    isPending: false,
  });

  const registerMutateAsync =
    assistantOverrides?.registerMutateAsync ??
    vi.fn().mockResolvedValue(assistantStatus);
  const useRegisterAssistant = (): MutationLike<AssistantRegisterBody, AssistantStatus> => ({
    mutateAsync: registerMutateAsync,
    isPending: false,
  });

  const ctxValue = {
    settings: { useSettings, useUpdateOrgSettings },
    assistant: {
      useAssistantStatus,
      useInitAssistant,
      useRepairAssistant,
      useRegisterAssistant,
      openSession: vi.fn().mockRejectedValue(new Error('no socket in dialog')),
    },
  } as unknown as Parameters<typeof DataContext.Provider>[0]['value'];

  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={['/orgs/alpha/dashboard']}>
        <Routes>
          <Route
            path="/orgs/:slug/dashboard"
            element={
              <DataContext.Provider value={ctxValue}>
                <SettingsDialog open onOpenChange={onClose} />
              </DataContext.Provider>
            }
          />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  vi.restoreAllMocks();
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
    expect(textInputs.length + numberInputs.length).toBeGreaterThanOrEqual(7);
  });

  test('shows restart-required badges for CLI paths and orchestration fields', async () => {
    renderDialog();

    const badges = screen.getAllByText('Restart required');
    expect(badges.length).toBe(7); // 4 CLI paths + max_orchestration_steps + queue_workers + protocol_dir

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
    const useAssistantStatus = (): QueryLike<AssistantStatus> => ({
      data: undefined,
      isLoading: true,
      isError: false,
      error: null,
    });
    const useInitAssistant = (): MutationLike<{ reconfigure: boolean }, AssistantStatus> => ({
      mutateAsync: vi.fn(),
      isPending: false,
    });
    const useRepairAssistant = (): MutationLike<void, AssistantStatus> => ({
      mutateAsync: vi.fn(),
      isPending: false,
    });
    const useRegisterAssistant = (): MutationLike<AssistantRegisterBody, AssistantStatus> => ({
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
                      assistant: {
                        useAssistantStatus,
                        useInitAssistant,
                        useRepairAssistant,
                        useRegisterAssistant,
                        openSession: vi.fn(),
                      },
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
    const useAssistantStatus = (): QueryLike<AssistantStatus> => ({
      data: undefined,
      isLoading: false,
      isError: false,
      error: null,
    });
    const useInitAssistant = (): MutationLike<{ reconfigure: boolean }, AssistantStatus> => ({
      mutateAsync: vi.fn(),
      isPending: false,
    });
    const useRepairAssistant = (): MutationLike<void, AssistantStatus> => ({
      mutateAsync: vi.fn(),
      isPending: false,
    });
    const useRegisterAssistant = (): MutationLike<AssistantRegisterBody, AssistantStatus> => ({
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
                      assistant: {
                        useAssistantStatus,
                        useInitAssistant,
                        useRepairAssistant,
                        useRegisterAssistant,
                        openSession: vi.fn(),
                      },
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

    // Threads cap input should show 500
    const capInput = screen.getByDisplayValue('500');
    expect(capInput).toBeInTheDocument();

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

  // ----------------------------------------------------------------
  // System Assistant section
  // ----------------------------------------------------------------

  test('renders System Assistant section with configured state', async () => {
    renderDialog();

    expect(screen.getByText('System Assistant')).toBeInTheDocument();
    expect(screen.getByText('Configured')).toBeInTheDocument();
    expect(screen.getByText('claude')).toBeInTheDocument();
    expect(
      screen.getByText(/\/rt\/system\/assistant\/workspace/),
    ).toBeInTheDocument();
  });

  test('shows uninitialized state with Initialize button', async () => {
    renderDialog(undefined, vi.fn(), vi.fn().mockResolvedValue(mockSnapshot), {
      status: { state: 'uninitialized', selected_executor: null, workspace_path: null },
    });

    expect(screen.getByText('Uninitialized')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /Initialize workspace/i })).toBeInTheDocument();
  });

  test('shows stale_or_broken state with detail and Repair button', async () => {
    renderDialog(undefined, vi.fn(), vi.fn().mockResolvedValue(mockSnapshot), {
      status: {
        state: 'stale_or_broken',
        selected_executor: 'codex',
        workspace_path: '/rt/system/assistant/workspace',
        detail: 'workspace missing AGENTS.md',
      },
    });

    expect(screen.getByText('Stale or broken')).toBeInTheDocument();
    expect(screen.getByText('workspace missing AGENTS.md')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /^Repair$/i })).toBeInTheDocument();
  });

  test('Initialize button calls init mutation', async () => {
    const initMutateAsync = vi.fn().mockResolvedValue({
      state: 'uninitialized' as const,
      selected_executor: null,
      workspace_path: '/rt/system/assistant/workspace',
      detail: null,
    });
    renderDialog(undefined, vi.fn(), vi.fn().mockResolvedValue(mockSnapshot), {
      status: { state: 'uninitialized', selected_executor: null, workspace_path: null },
      initMutateAsync,
    });

    const initBtn = screen.getByRole('button', { name: /Initialize workspace/i });
    fireEvent.click(initBtn);

    await vi.waitFor(() => {
      expect(initMutateAsync).toHaveBeenCalledTimes(1);
    });
    expect(initMutateAsync).toHaveBeenCalledWith({ reconfigure: false });
  });

  test('Repair button calls repair mutation', async () => {
    const repairMutateAsync = vi.fn().mockResolvedValue({
      state: 'configured' as const,
      selected_executor: 'codex',
      workspace_path: '/rt/system/assistant/workspace',
      detail: null,
    });
    renderDialog(undefined, vi.fn(), vi.fn().mockResolvedValue(mockSnapshot), {
      status: {
        state: 'stale_or_broken',
        selected_executor: 'codex',
        workspace_path: '/rt/system/assistant/workspace',
        detail: 'workspace missing AGENTS.md',
      },
      repairMutateAsync,
    });

    const repairBtn = screen.getByRole('button', { name: /^Repair$/i });
    fireEvent.click(repairBtn);

    await vi.waitFor(() => {
      expect(repairMutateAsync).toHaveBeenCalledTimes(1);
    });
  });

  test('configured state shows link to assistant terminal page', async () => {
    renderDialog();

    const link = screen.getByRole('link', { name: /open terminal/i });
    expect(link).toBeInTheDocument();
    expect(link.getAttribute('href')).toContain('/assistant');
  });
});
