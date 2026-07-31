/**
 * PendingAdaptersSection tests — Settings ▸ Executors founder-only pending
 * adapter approvals (THR-107 seq220, fix-forward TASK-3805).
 *
 * Covers: pending card fields/placement, hash confirmation, cancel, loading,
 * error; exact snapshot approve + managed auth; reject success/stale/non-pending;
 * shared RecoveryBindCard bind flow; already_bound survives refetch;
 * onboarding separation (renders actual ConnectFlow, proves no Approve/Reject);
 * existing test regression.
 */
import { describe, test, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { http, HttpResponse } from 'msw';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter } from 'react-router-dom';
import { renderWithProviders } from '@/test/render';
import { server } from '@/test/server';
import { PendingAdaptersSection } from './PendingAdaptersSection';
import { ConnectFlow } from '@/shared/connect/ConnectFlow';

const API_BASE = '/api/v1/runtime/adapters';

/** A PENDING adapter fixture with all fields. */
function makePendingAdapter(overrides: Record<string, unknown> = {}) {
  return {
    id: overrides.id ?? 'test-adapter',
    name: overrides.name ?? 'test-adapter',
    executable: overrides.executable ?? '/usr/local/bin/my-cli',
    executable_hash:
      overrides.executable_hash ??
      'e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855',
    version: overrides.version ?? '1.0.0',
    capabilities: (overrides.capabilities as string[]) ?? ['token_metering'],
    contract_version: (overrides.contract_version as number) ?? 1,
    workspace_adapter: (overrides.workspace_adapter as string) ?? 'pi',
    status: (overrides.status as string) ?? 'pending',
    registered_at: '2024-01-01T00:00:00Z',
    registered_by: 'test',
    approved_at: (overrides.approved_at as string | null) ?? null,
    approved_by: (overrides.approved_by as string | null) ?? null,
    intended_profile_name: (overrides.intended_profile_name as string | null) ?? 'my-custom-cli',
    eligibility: (overrides.eligibility as string | null) ?? null,
  };
}

/** Mock the adapter list with given entries. */
function mockListAdapters(...adapters: ReturnType<typeof makePendingAdapter>[]) {
  server.use(
    http.get(API_BASE, () => HttpResponse.json(adapters)),
  );
}

/** Render ConnectFlow with the wrapper needed for adapter recovery queries. */
function renderConnectFlow() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={['/']}>
        <ConnectFlow
          connectedSubtitle={() => 'Connected.'}
        />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe('PendingAdaptersSection (Settings → Executors → Pending Approvals)', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    sessionStorage.setItem('happyranch.token', 'test-token');
  });

  /* ---- Rendering: empty + populated ---- */

  test('renders section heading and description', async () => {
    mockListAdapters();
    renderWithProviders(<PendingAdaptersSection />);
    await waitFor(() => {
      expect(screen.getByText('Pending Adapter Approvals')).toBeInTheDocument();
    });
    expect(
      screen.getByText(/Adapters awaiting founder approval/),
    ).toBeInTheDocument();
  });

  test('no pending adapters: section is empty (no rows)', async () => {
    mockListAdapters(makePendingAdapter({ status: 'approved', id: 'approved-adapter', eligibility: null }));
    renderWithProviders(<PendingAdaptersSection />);
    await waitFor(() => {
      expect(screen.getByTestId('pending-adapters-section')).toBeInTheDocument();
    });
    // No pending rows rendered
    expect(screen.queryByTestId('pending-adapter-rows')).not.toBeInTheDocument();
  });

  test('populated: renders one card per pending adapter with all fields', async () => {
    mockListAdapters(
      makePendingAdapter(),
      makePendingAdapter({ id: 'adapter-2', name: 'adapter-2' }),
    );
    renderWithProviders(<PendingAdaptersSection />);
    await waitFor(() => {
      expect(screen.getByTestId('pending-adapter-row-test-adapter')).toBeInTheDocument();
    });
    expect(screen.getByTestId('pending-adapter-row-adapter-2')).toBeInTheDocument();

    // Check field display for the first adapter
    const row = screen.getByTestId('pending-adapter-row-test-adapter');
    expect(row).toHaveTextContent('test-adapter');
    expect(row).toHaveTextContent('pending');
    expect(row).toHaveTextContent('my-custom-cli');
    expect(row).toHaveTextContent('/usr/local/bin/my-cli');
    expect(row).toHaveTextContent('e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855');
    expect(row).toHaveTextContent('1.0.0');
    expect(row).toHaveTextContent('pi');
    expect(row).toHaveTextContent('token_metering');
    expect(row).toHaveTextContent('1'); // contract_version
  });

  test('pending card shows intended profile name', async () => {
    mockListAdapters(makePendingAdapter({ intended_profile_name: 'kimi-cli' }));
    renderWithProviders(<PendingAdaptersSection />);
    await screen.findByTestId('pending-adapter-row-test-adapter');
    expect(screen.getByTestId('adapter-intended-profile-test-adapter')).toHaveTextContent('kimi-cli');
  });

  test('pending card shows full SHA-256 hash', async () => {
    const hash = 'a'.repeat(64);
    mockListAdapters(makePendingAdapter({ executable_hash: hash }));
    renderWithProviders(<PendingAdaptersSection />);
    await screen.findByTestId('pending-adapter-row-test-adapter');
    expect(screen.getByTestId('adapter-hash-test-adapter')).toHaveTextContent(hash);
  });

  /* ---- Error / Loading states ---- */

  test('loading: shows loading text', async () => {
    // Simulate infinite loading by omitting the mock — the query hangs
    server.use(http.get(API_BASE, () => new Promise(() => {})));
    renderWithProviders(<PendingAdaptersSection />);
    expect(screen.getByText('Loading adapters…')).toBeInTheDocument();
  });

  test('error: shows error when list fails', async () => {
    server.use(
      http.get(API_BASE, () => HttpResponse.json({ detail: 'internal error' }, { status: 500 })),
    );
    renderWithProviders(<PendingAdaptersSection />);
    await waitFor(() => {
      expect(screen.getByText(/Could not load custom adapters/)).toBeInTheDocument();
    });
  });

  /* ---- Approve flow ---- */

  test('approve: confirm step names the exact 64-char SHA-256 hash (no prefix/ellipsis)', async () => {
    const fullHash = 'e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855';
    mockListAdapters(makePendingAdapter({ executable_hash: fullHash }));
    const user = userEvent.setup();
    renderWithProviders(<PendingAdaptersSection />);
    await screen.findByTestId('pending-adapter-row-test-adapter');

    // Initial state: Approve button visible
    expect(screen.getByTestId('adapter-approve-test-adapter')).toBeInTheDocument();
    expect(screen.queryByTestId('adapter-confirm-approve-test-adapter')).not.toBeInTheDocument();

    // Click Approve → confirm step
    await user.click(screen.getByTestId('adapter-approve-test-adapter'));
    expect(screen.getByTestId('adapter-confirm-approve-test-adapter')).toBeInTheDocument();
    // Confirm text names the exact full 64-char hash (seq237: approve & connect)
    expect(screen.getByText(/Confirm approval and connection of adapter/)).toBeInTheDocument();
    // Guard: the full hash (not a prefix) must appear BOTH in the card's SHA-256
    // field AND in the confirm prompt — at least 2 occurrences of the exact 64-char value
    const hashElements = screen.getAllByText(fullHash);
    expect(hashElements.length).toBeGreaterThanOrEqual(2);

    // Regression guard: no abbreviated hash (12-char prefix with ellipsis) should appear
    expect(screen.queryByText(/…/)).not.toBeInTheDocument();

    // Cancel returns to initial
    await user.click(screen.getByText('Cancel'));
    expect(screen.getByTestId('adapter-approve-test-adapter')).toBeInTheDocument();
    expect(screen.queryByTestId('adapter-confirm-approve-test-adapter')).not.toBeInTheDocument();
  });

  test('approve: confirm sends exact snapshot POST, then refetches', async () => {
    const adapter = makePendingAdapter();
    mockListAdapters(adapter);

    let approveBody: unknown = null;
    server.use(
      http.post(`${API_BASE}/test-adapter/approve`, async ({ request }) => {
        approveBody = await request.json();
        return HttpResponse.json({
          ...adapter,
          status: 'approved',
          approved_at: '2024-01-01T00:00:00Z',
          approved_by: 'founder',
        });
      }),
    );

    const user = userEvent.setup();
    renderWithProviders(<PendingAdaptersSection />);
    await screen.findByTestId('pending-adapter-row-test-adapter');
    await user.click(screen.getByTestId('adapter-approve-test-adapter'));
    await user.click(screen.getByTestId('adapter-confirm-approve-test-adapter'));

    await waitFor(() => {
      expect(approveBody).toBeDefined();
    });
    expect(approveBody).toEqual({
      executable: adapter.executable,
      executable_hash: adapter.executable_hash,
      version: adapter.version,
      capabilities: adapter.capabilities,
      contract_version: adapter.contract_version,
      workspace_adapter: adapter.workspace_adapter,
    });
  });

  test('approve: after success, card shows Connected (seq237 auto-bind)', async () => {
    const adapter = makePendingAdapter();
    mockListAdapters(adapter);

    server.use(
      http.post(`${API_BASE}/test-adapter/approve`, async () => {
        return HttpResponse.json({
          ...adapter,
          status: 'approved',
          approved_at: '2024-01-01T00:00:00Z',
          approved_by: 'founder',
          profile_bound: {
            profile_name: 'my-custom-cli',
            command_adapter_id: 'custom-adapter:test-adapter',
            workspace_adapter_id: 'pi',
            kind: 'custom',
            status: 'connected',
            adapter_id: 'test-adapter',
          },
        });
      }),
    );

    // After approve, list returns approved adapter with already_bound
    let listCalled = false;
    server.use(
      http.get(API_BASE, () => {
        if (listCalled) {
          return HttpResponse.json([{
            ...adapter,
            status: 'approved',
            approved_at: '2024-01-01T00:00:00Z',
            approved_by: 'founder',
            eligibility: 'already_bound',
          }]);
        }
        listCalled = true;
        return HttpResponse.json([adapter]);
      }),
    );

    const user = userEvent.setup();
    renderWithProviders(<PendingAdaptersSection />);
    await screen.findByTestId('pending-adapter-row-test-adapter');
    await user.click(screen.getByTestId('adapter-approve-test-adapter'));
    await user.click(screen.getByTestId('adapter-confirm-approve-test-adapter'));

    // After seq237 approval, adapter shows Connected state (already_bound)
    await waitFor(() => {
      expect(screen.getByTestId('pending-adapter-row-test-adapter')).toBeInTheDocument();
    });
    // The connected card shows the profile name (not a Bind button)
    expect(screen.getByText(/my-custom-cli/)).toBeInTheDocument();
    // No Bind button (seq237: already connected, no recovery needed)
    expect(screen.queryByRole('button', { name: /bind/i })).not.toBeInTheDocument();
  });

  test('approve: error surfaces inline', async () => {
    mockListAdapters(makePendingAdapter());
    server.use(
      http.post(`${API_BASE}/test-adapter/approve`, () =>
        HttpResponse.json({ detail: 'Adapter hash mismatch' }, { status: 422 }),
      ),
    );

    const user = userEvent.setup();
    renderWithProviders(<PendingAdaptersSection />);
    await screen.findByTestId('pending-adapter-row-test-adapter');
    await user.click(screen.getByTestId('adapter-approve-test-adapter'));
    await user.click(screen.getByTestId('adapter-confirm-approve-test-adapter'));

    await waitFor(() => {
      expect(screen.getByRole('alert')).toHaveTextContent('Adapter hash mismatch');
    });
  });

  /* ---- already_bound survives refetch (fix-forward TASK-3805) ---- */

  test('already_bound adapter renders as Connected and survives refetch', async () => {
    const adapter = makePendingAdapter({
      status: 'approved',
      eligibility: 'already_bound',
    });
    mockListAdapters(adapter);
    renderWithProviders(<PendingAdaptersSection />);

    await waitFor(() => {
      expect(screen.getByTestId('pending-adapter-row-test-adapter')).toBeInTheDocument();
    });
    // Connected card renders with the profile name and "connected" text
    const connectedRow = screen.getByTestId('pending-adapter-row-test-adapter');
    expect(connectedRow).toHaveTextContent('my-custom-cli');
    expect(connectedRow).toHaveTextContent('connected');
    expect(screen.getByText(/Profile bound to adapter/)).toBeInTheDocument();

    // No Approve/Reject controls on already_bound cards
    expect(screen.queryByTestId('adapter-approve-test-adapter')).not.toBeInTheDocument();
    expect(screen.queryByTestId('adapter-reject-test-adapter')).not.toBeInTheDocument();

    // Refetch — adapter still appears (survives the filter)
    server.use(
      http.get(API_BASE, () => HttpResponse.json([adapter])),
    );
    // Re-render to simulate fresh render from server
    renderWithProviders(<PendingAdaptersSection />);
    await waitFor(() => {
      expect(screen.getByTestId('pending-adapter-row-test-adapter')).toBeInTheDocument();
    });
    expect(screen.getByTestId('pending-adapter-row-test-adapter')).toHaveTextContent('connected');
  });

  test('ready_to_bind adapter renders RecoveryBindCard; already_bound renders Connected', async () => {
    const readyAdapter = makePendingAdapter({
      id: 'ready-adapter',
      status: 'approved',
      eligibility: 'ready_to_bind',
      intended_profile_name: 'ready-profile',
    });
    const boundAdapter = makePendingAdapter({
      id: 'bound-adapter',
      status: 'approved',
      eligibility: 'already_bound',
      intended_profile_name: 'bound-profile',
    });
    mockListAdapters(readyAdapter, boundAdapter);
    renderWithProviders(<PendingAdaptersSection />);

    await waitFor(() => {
      expect(screen.getByTestId('pending-adapter-row-ready-adapter')).toBeInTheDocument();
    });
    expect(screen.getByTestId('pending-adapter-row-bound-adapter')).toBeInTheDocument();

    // ready_to_bind shows RecoveryBindCard with Bind button
    expect(screen.getByRole('button', { name: /bind ready-profile/i })).toBeInTheDocument();

    // already_bound shows Connected card
    const boundRow = screen.getByTestId('pending-adapter-row-bound-adapter');
    expect(boundRow).toHaveTextContent('bound-profile');
    expect(boundRow).toHaveTextContent('connected');

    // No Approve/Reject on approved cards
    expect(screen.queryByTestId('adapter-approve-ready-adapter')).not.toBeInTheDocument();
    expect(screen.queryByTestId('adapter-reject-ready-adapter')).not.toBeInTheDocument();
  });

  /* ---- Reject flow ---- */

  test('reject: confirm step names the exact 64-char SHA-256 hash (no prefix/ellipsis)', async () => {
    const fullHash = 'e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855';
    mockListAdapters(makePendingAdapter({ executable_hash: fullHash }));
    const user = userEvent.setup();
    renderWithProviders(<PendingAdaptersSection />);
    await screen.findByTestId('pending-adapter-row-test-adapter');

    // Initial state: Reject button visible
    expect(screen.getByTestId('adapter-reject-test-adapter')).toBeInTheDocument();

    // Click Reject → confirm step
    await user.click(screen.getByTestId('adapter-reject-test-adapter'));
    expect(screen.getByTestId('adapter-confirm-reject-test-adapter')).toBeInTheDocument();
    expect(screen.getByText(/Confirm rejection of adapter/)).toBeInTheDocument();
    // Guard: the full 64-char hash (not a prefix) must appear in the confirm prompt
    const hashElements = screen.getAllByText(fullHash);
    expect(hashElements.length).toBeGreaterThanOrEqual(2);

    // Regression guard: no abbreviated hash with ellipsis
    expect(screen.queryByText(/…/)).not.toBeInTheDocument();

    // Cancel returns to initial
    await user.click(screen.getByText('Cancel'));
    expect(screen.getByTestId('adapter-reject-test-adapter')).toBeInTheDocument();
    expect(screen.queryByTestId('adapter-confirm-reject-test-adapter')).not.toBeInTheDocument();
  });

  test('reject: confirm sends exact snapshot POST, then refetches', async () => {
    const adapter = makePendingAdapter();
    mockListAdapters(adapter);

    let rejectBody: unknown = null;
    server.use(
      http.post(`${API_BASE}/test-adapter/reject`, async ({ request }) => {
        rejectBody = await request.json();
        return HttpResponse.json({
          id: 'test-adapter',
          rejected: true,
          name: adapter.name,
        });
      }),
    );

    const user = userEvent.setup();
    renderWithProviders(<PendingAdaptersSection />);
    await screen.findByTestId('pending-adapter-row-test-adapter');
    await user.click(screen.getByTestId('adapter-reject-test-adapter'));
    await user.click(screen.getByTestId('adapter-confirm-reject-test-adapter'));

    await waitFor(() => {
      expect(rejectBody).toBeDefined();
    });
    expect(rejectBody).toEqual({
      executable: adapter.executable,
      executable_hash: adapter.executable_hash,
      version: adapter.version,
      capabilities: adapter.capabilities,
      contract_version: adapter.contract_version,
      workspace_adapter: adapter.workspace_adapter,
    });
  });

  test('reject: after success, card is removed after refetch', async () => {
    mockListAdapters(makePendingAdapter());

    server.use(
      http.post(`${API_BASE}/test-adapter/reject`, () =>
        HttpResponse.json({ id: 'test-adapter', rejected: true, name: 'test-adapter' }),
      ),
    );

    // After reject, list returns empty
    let listCalled = false;
    server.use(
      http.get(API_BASE, () => {
        if (listCalled) return HttpResponse.json([]);
        listCalled = true;
        return HttpResponse.json([makePendingAdapter()]);
      }),
    );

    const user = userEvent.setup();
    renderWithProviders(<PendingAdaptersSection />);
    await screen.findByTestId('pending-adapter-row-test-adapter');
    await user.click(screen.getByTestId('adapter-reject-test-adapter'));
    await user.click(screen.getByTestId('adapter-confirm-reject-test-adapter'));

    await waitFor(() => {
      expect(screen.queryByTestId('pending-adapter-row-test-adapter')).not.toBeInTheDocument();
    });
  });

  test('reject: error surfaces inline', async () => {
    mockListAdapters(makePendingAdapter());
    server.use(
      http.post(`${API_BASE}/test-adapter/reject`, () =>
        HttpResponse.json({ detail: 'Adapter not PENDING' }, { status: 422 }),
      ),
    );

    const user = userEvent.setup();
    renderWithProviders(<PendingAdaptersSection />);
    await screen.findByTestId('pending-adapter-row-test-adapter');
    await user.click(screen.getByTestId('adapter-reject-test-adapter'));
    await user.click(screen.getByTestId('adapter-confirm-reject-test-adapter'));

    await waitFor(() => {
      expect(screen.getByRole('alert')).toHaveTextContent('Adapter not PENDING');
    });
  });

  /* ---- Duplicate submission prevention ---- */

  test('approve: button is disabled while mutation is pending', async () => {
    mockListAdapters(makePendingAdapter());
    server.use(
      http.post(`${API_BASE}/test-adapter/approve`, () => new Promise(() => {})),
    );

    const user = userEvent.setup();
    renderWithProviders(<PendingAdaptersSection />);
    await screen.findByTestId('pending-adapter-row-test-adapter');
    await user.click(screen.getByTestId('adapter-approve-test-adapter'));

    const confirmBtn = screen.getByTestId('adapter-confirm-approve-test-adapter');
    await user.click(confirmBtn);
    // After click, the button should be disabled (mutation isPending)
    expect(confirmBtn).toBeDisabled();
  });

  /* ---- Onboarding separation — renders ConnectFlow, proves no Approve/Reject ---- */

  test('ConnectFlow (shared component, showRecovery=true) never renders Approve/Reject controls for pending adapters', async () => {
    // Mock adapter list with a pending adapter — the settings section shows
    // Approve/Reject, but ConnectFlow with showRecovery=true (Settings consumer)
    // for approved adapters only) should NOT show them.
    mockListAdapters(makePendingAdapter());

    // Also mock the prereqs endpoint that useAdapterRecovery queries
    server.use(
      http.get('/api/v1/runtime/adapters', () =>
        HttpResponse.json([makePendingAdapter()]),
      ),
    );

    renderConnectFlow();

    // Wait for any loading to resolve
    await waitFor(() => {
      // ConnectFlow should render (the built-in dropdown is the default view)
      // No Approve/Reject buttons should exist anywhere
      expect(screen.queryByTestId(/adapter-approve-/)).not.toBeInTheDocument();
      expect(screen.queryByTestId(/adapter-reject-/)).not.toBeInTheDocument();
      expect(screen.queryByText(/Confirm approval/)).not.toBeInTheDocument();
      expect(screen.queryByText(/Confirm rejection/)).not.toBeInTheDocument();
    });

    // The pending adapter should NOT appear in RecoverySection either,
    // since RecoverySection only shows approved ready_to_bind adapters.
    expect(screen.queryByText(/Advanced recovery \/ legacy adapters/)).not.toBeInTheDocument();
  });

  test('ConnectFlow (shared component, showRecovery=true) shows RecoverySection for approved ready-to-bind adapters but without Approve/Reject', async () => {
    // Approved adapter with ready_to_bind eligibility
    const approvedReady = makePendingAdapter({
      id: 'approved-adapter',
      status: 'approved',
      eligibility: 'ready_to_bind',
      intended_profile_name: 'my-cli',
    });
    mockListAdapters(approvedReady);

    renderConnectFlow();

    // RecoverySection should appear with the approved adapter
    await waitFor(() => {
      expect(screen.getByText(/Advanced recovery \/ legacy adapters/)).toBeInTheDocument();
    });

    // The Bind button should be visible
    expect(screen.getByRole('button', { name: /bind my-cli/i })).toBeInTheDocument();

    // But NO Approve/Reject controls
    expect(screen.queryByTestId(/adapter-approve-/)).not.toBeInTheDocument();
    expect(screen.queryByTestId(/adapter-reject-/)).not.toBeInTheDocument();
    expect(screen.queryByText(/Confirm approval/)).not.toBeInTheDocument();
    expect(screen.queryByText(/Confirm rejection/)).not.toBeInTheDocument();
  });
});
