import { describe, expect, test, vi, beforeEach } from 'vitest';
import { render, screen } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { DataContext } from '@/design-system/providers/DataContext';
import { SettingsDialog } from './SettingsDialog';
import type { SettingsSnapshot, SystemSettings, OrgSettings } from '@/lib/api/types';
import type { QueryLike } from '@/design-system/providers/DataContext';

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

  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={['/orgs/alpha/dashboard']}>
        <Routes>
          <Route
            path="/orgs/:slug/dashboard"
            element={
              <DataContext.Provider
                value={
                  {
                    settings: { useSettings },
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
    // "Session timeout (s)" appears in both System and Org sections
    expect(screen.getAllByText(/Session timeout \(s\)/).length).toBeGreaterThanOrEqual(2);
    expect(screen.getByText('1800')).toBeInTheDocument();

    // Org section
    expect(screen.getByText('Org')).toBeInTheDocument();
    expect(screen.getByText(/Dreaming/)).toBeInTheDocument();
    expect(screen.getByText(/Threads/)).toBeInTheDocument();

    // Threads nested values
    expect(screen.getByText('500')).toBeInTheDocument();
  });

  test('shows restart-required badges for CLI paths and orchestration fields', async () => {
    renderDialog();

    const badges = screen.getAllByText('Restart required');
    expect(badges.length).toBe(7); // 4 CLI paths + max_orchestration_steps + queue_workers + protocol_dir

    // Session timeout should NOT have a restart badge
    // Use querySelectorAll to check each "Session timeout (s)" row individually
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
                      settings: { useSettings },
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
                      settings: { useSettings },
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

  test('renders null values as em dash in Org section', () => {
    const nullOrg: OrgSettings = {
      session_timeout_seconds: null,
      dreaming: {
        enabled: false,
        schedule: { time: '02:00', timezone: 'UTC' },
        catch_up_on_startup: false,
        agents: { mode: 'all', include: [], exclude: [] },
      },
      threads: {
        enabled: true,
        default_turn_cap: 500,
        invocation_timeout_seconds: null,
      },
    };

    renderDialog({ org: nullOrg });

    // Find em dash for null values
    const dashes = screen.getAllByText('—');
    expect(dashes.length).toBeGreaterThanOrEqual(2); // session_timeout + invocation_timeout (and possibly include/exclude)
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
});
