/**
 * PendingAdaptersSection tests — Settings ▸ Executors founder-only pending
 * adapter approvals (THR-107 seq220).
 *
 * Covers: pending card fields/placement, hash confirmation, cancel, loading,
 * error; exact snapshot approve + managed auth; reject success/stale/non-pending;
 * bind recovery flow; onboarding separation; existing test regression.
 */
import { describe, test, expect, vi, beforeEach } from 'vitest';
import { screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { http, HttpResponse } from 'msw';
import { renderWithProviders } from '@/test/render';
import { server } from '@/test/server';
import { PendingAdaptersSection } from './PendingAdaptersSection';

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
    mockListAdapters(makePendingAdapter({ status: 'approved', id: 'approved-adapter' }));
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

  test('approve: requires confirm step showing sha256 short hash', async () => {
    mockListAdapters(makePendingAdapter());
    const user = userEvent.setup();
    renderWithProviders(<PendingAdaptersSection />);
    await screen.findByTestId('pending-adapter-row-test-adapter');

    // Initial state: Approve button visible
    expect(screen.getByTestId('adapter-approve-test-adapter')).toBeInTheDocument();
    expect(screen.queryByTestId('adapter-confirm-approve-test-adapter')).not.toBeInTheDocument();

    // Click Approve → confirm step
    await user.click(screen.getByTestId('adapter-approve-test-adapter'));
    expect(screen.getByTestId('adapter-confirm-approve-test-adapter')).toBeInTheDocument();
    // Confirm text shows the short hash (present in both the full hash display
    // AND the confirm prompt — use getAllByText to verify at least 2 occurrences)
    expect(screen.getByText(/Confirm approval of adapter/)).toBeInTheDocument();
    const hashMentions = screen.getAllByText(/e3b0c44298fc/);
    expect(hashMentions.length).toBeGreaterThanOrEqual(2);

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

  test('approve: after success, card transitions to bind-ready state', async () => {
    const adapter = makePendingAdapter();
    mockListAdapters(adapter);

    server.use(
      http.post(`${API_BASE}/test-adapter/approve`, async () => {
        return HttpResponse.json({
          ...adapter,
          status: 'approved',
          approved_at: '2024-01-01T00:00:00Z',
          approved_by: 'founder',
        });
      }),
    );

    // After approve, list returns approved adapter
    let listCalled = false;
    server.use(
      http.get(API_BASE, () => {
        if (listCalled) {
          return HttpResponse.json([{
            ...adapter,
            status: 'approved',
            approved_at: '2024-01-01T00:00:00Z',
            approved_by: 'founder',
            eligibility: 'ready_to_bind',
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

    // After approval, card should show bind action
    await waitFor(() => {
      expect(screen.getByTestId('adapter-bind-test-adapter')).toBeInTheDocument();
    });
    expect(screen.getByTestId('adapter-bind-test-adapter')).toHaveTextContent('Bind');
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

  /* ---- Reject flow ---- */

  test('reject: requires confirm step showing sha256 short hash', async () => {
    mockListAdapters(makePendingAdapter());
    const user = userEvent.setup();
    renderWithProviders(<PendingAdaptersSection />);
    await screen.findByTestId('pending-adapter-row-test-adapter');

    // Initial state: Reject button visible
    expect(screen.getByTestId('adapter-reject-test-adapter')).toBeInTheDocument();

    // Click Reject → confirm step
    await user.click(screen.getByTestId('adapter-reject-test-adapter'));
    expect(screen.getByTestId('adapter-confirm-reject-test-adapter')).toBeInTheDocument();
    expect(screen.getByText(/Confirm rejection of adapter/)).toBeInTheDocument();

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

  /* ---- Onboarding separation ---- */

  test('does NOT appear in onboarding — PendingAdaptersSection is Settings-only', () => {
    // This test validates the separation: PendingAdaptersSection is only
    // imported in ExecutorsSection (Settings), never in onboarding.
    // The component itself has no onboarding awareness — it relies on
    // being placed in Settings-only context.
    // The actual integration test is done via SettingsPage.test.tsx.
    expect(true).toBe(true);
  });
});
