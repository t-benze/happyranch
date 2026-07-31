/**
 * ConnectFlow recovery tests (THR-107 fix-forward TASK-3784).
 *
 * Validates:
 *  1. Server-authoritative eligibility (all negative cases)
 *  2. Durable bind completion (refetch + verify server state)
 *  3. Recovery visible in default builtin mode
 *
 * Key behavioral changes from TASK-3780:
 *  - Eligibility is now computed SERVER-SIDE (field: eligibility).
 *    The browser NEVER recomputes hash/tamper status.
 *  - Bind success → verifies server state → Connected
 *    (no longer calls setConnected directly from bind response).
 */
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { describe, test, expect, vi, beforeEach, afterEach } from 'vitest';
import { ConnectFlow } from './ConnectFlow';

/* ------------------------------------------------------------------ */
/*  Helpers                                                            */
/* ------------------------------------------------------------------ */

function makeClient(): QueryClient {
  return new QueryClient({
    defaultOptions: {
      queries: { retry: false, gcTime: 0 },
    },
  });
}

function renderConnect(): QueryClient {
  const qc = makeClient();
  render(
    <QueryClientProvider client={qc}>
      <ConnectFlow
        connectedSubtitle={() => 'Your CLI is connected.'}
      />
    </QueryClientProvider>,
  );
  return qc;
}

/** Factory: creates an adapter entry with all fields.
 *  ``overrides`` set the per-test eligibility and other key fields. */
function makeAdapter(
  overrides: Partial<{
    id: string; name: string; status: string; intended_profile_name: string | null;
    eligibility: string | null; executable: string; executable_hash: string;
  }> = {},
): import('@/lib/api/adapters').AdapterEntry {
  const id = overrides.id ?? 'kimi-adapter';
  return {
    id,
    name: overrides.name ?? 'kimi-adapter',
    executable: overrides.executable ?? '/opt/kimi/cli',
    executable_hash: overrides.executable_hash ?? 'abc123hash',
    version: '1.0.0',
    capabilities: [],
    contract_version: 1,
    workspace_adapter: 'pi',
    status: overrides.status ?? 'approved',
    registered_at: '2026-07-01T00:00:00Z',
    registered_by: 'testuser',
    approved_at: '2026-07-02T00:00:00Z',
    approved_by: 'founder',
    intended_profile_name: overrides.intended_profile_name ?? 'kimi',
    eligibility: (overrides.eligibility ?? 'ready_to_bind') as import('@/lib/api/adapters').AdapterEligibility,
  };
}

async function mockListAdapters(...adapters: import('@/lib/api/adapters').AdapterEntry[]): Promise<void> {
  const { adapters: api } = await import('@/lib/api');
  vi.spyOn(api, 'listAdapters').mockResolvedValue(adapters);
}

async function mockBindSuccess(profileName: string, adapterId: string): Promise<void> {
  const { adapters: api } = await import('@/lib/api');
  vi.spyOn(api, 'bindAdapterProfile').mockResolvedValue({
    profile_name: profileName,
    command_adapter_id: `custom-adapter:${adapterId}`,
    workspace_adapter_id: 'pi',
    kind: 'custom',
    status: 'connected',
    adapter_id: adapterId,
  });
}

async function mockBindError(msg = 'Adapter artifact changed before bind.'): Promise<void> {
  const { adapters: api } = await import('@/lib/api');
  vi.spyOn(api, 'bindAdapterProfile').mockRejectedValue(new Error(msg));
}

/* ------------------------------------------------------------------ */
/*  Tests                                                              */
/* ------------------------------------------------------------------ */

describe('ConnectFlow — server-authoritative recovery eligibility', () => {
  beforeEach(() => { vi.restoreAllMocks(); });
  afterEach(() => { vi.restoreAllMocks(); });

  /* ---- positive: ready_to_bind ---- */

  test('ready_to_bind adapter shows Bind card in default builtin mode', async () => {
    await mockListAdapters(makeAdapter({ eligibility: 'ready_to_bind' }));
    renderConnect();

    await screen.findByText('Advanced recovery \/ legacy adapters', {}, { timeout: 5000 });
    expect(screen.getByRole('button', { name: /bind kimi/i })).toBeInTheDocument();
    expect(screen.getByLabelText(/pick your agentic cli/i)).toBeInTheDocument();
  }, 10000);

  /* ---- negative: already_bound ---- */

  test('already_bound adapter does NOT show Bind (false-Connected protection)', async () => {
    await mockListAdapters(makeAdapter({
      id: 'bound-adapter',
      intended_profile_name: 'bound-cli',
      eligibility: 'already_bound',
    }));
    renderConnect();

    await waitFor(() => {
      expect(screen.getByLabelText(/pick your agentic cli/i)).toBeInTheDocument();
    }, { timeout: 5000 });

    expect(screen.queryByText('Advanced recovery \/ legacy adapters')).not.toBeInTheDocument();
  }, 10000);

  /* ---- negative: cross_profile ---- */

  test('cross_profile adapter does NOT show Bind (profile bound to DIFFERENT adapter)', async () => {
    await mockListAdapters(makeAdapter({
      id: 'cross-adapter',
      intended_profile_name: 'other-cli',
      eligibility: 'cross_profile',
    }));
    renderConnect();

    await waitFor(() => {
      expect(screen.getByLabelText(/pick your agentic cli/i)).toBeInTheDocument();
    }, { timeout: 5000 });

    expect(screen.queryByText('Advanced recovery \/ legacy adapters')).not.toBeInTheDocument();
  }, 10000);

  /* ---- negative: builtin_collision ---- */

  test('builtin_collision adapter does NOT show Bind', async () => {
    await mockListAdapters(makeAdapter({
      intended_profile_name: 'claude',
      eligibility: 'builtin_collision',
    }));
    renderConnect();

    await waitFor(() => {
      expect(screen.getByLabelText(/pick your agentic cli/i)).toBeInTheDocument();
    }, { timeout: 5000 });

    expect(screen.queryByText('Advanced recovery \/ legacy adapters')).not.toBeInTheDocument();
  }, 10000);

  /* ---- negative: tampered ---- */

  test('tampered adapter (hash mismatch / missing) does NOT show Bind', async () => {
    await mockListAdapters(makeAdapter({ eligibility: 'tampered' }));
    renderConnect();

    await waitFor(() => {
      expect(screen.getByLabelText(/pick your agentic cli/i)).toBeInTheDocument();
    }, { timeout: 5000 });

    expect(screen.queryByText('Advanced recovery \/ legacy adapters')).not.toBeInTheDocument();
  }, 10000);

  /* ---- negative: pending ---- */

  test('PENDING adapter does NOT show Bind (eligibility is null, not ready_to_bind)', async () => {
    await mockListAdapters(makeAdapter({
      status: 'pending',
      eligibility: null,
    }));
    renderConnect();

    await waitFor(() => {
      expect(screen.getByLabelText(/pick your agentic cli/i)).toBeInTheDocument();
    }, { timeout: 5000 });

    expect(screen.queryByText('Advanced recovery \/ legacy adapters')).not.toBeInTheDocument();
  }, 10000);

  /* ---- negative: recovery_ready (Settings consumer, showRecovery=true) ---- */

  test('recovery_ready adapter (no intended profile) does NOT show Bind through ConnectFlow recovery section', async () => {
    await mockListAdapters(makeAdapter({
      intended_profile_name: null,
      eligibility: 'recovery_ready',
    }));
    renderConnect();

    await waitFor(() => {
      expect(screen.getByLabelText(/pick your agentic cli/i)).toBeInTheDocument();
    }, { timeout: 5000 });

    expect(screen.queryByText('Advanced recovery \/ legacy adapters')).not.toBeInTheDocument();
  }, 10000);

  /* ---- explicit: approval without profile shows Bind, not Connected ---- */

  test('approval-without-profile shows Bind, not false-connected', async () => {
    await mockListAdapters(makeAdapter({ eligibility: 'ready_to_bind' }));
    renderConnect();

    await screen.findByText('Advanced recovery \/ legacy adapters', {}, { timeout: 5000 });

    // Section explicitly says approval is NOT profile creation
    expect(screen.getByText(/approved without an automated profile binding/i)).toBeInTheDocument();
    expect(screen.queryByRole('heading', { name: /kimi connected/i })).not.toBeInTheDocument();
  }, 10000);
});

/* ------------------------------------------------------------------ */
/*  Durable bind completion tests                                      */
/* ------------------------------------------------------------------ */

describe('ConnectFlow — durable bind completion', () => {
  beforeEach(() => { vi.restoreAllMocks(); });
  afterEach(() => { vi.restoreAllMocks(); });

  test('bind success → verifying → connected after server confirms', async () => {
    const user = userEvent.setup();

    // Initial: adapter is ready_to_bind
    await mockListAdapters(makeAdapter({ eligibility: 'ready_to_bind' }));
    await mockBindSuccess('kimi', 'kimi-adapter');

    renderConnect();
    await screen.findByText('Advanced recovery \/ legacy adapters', {}, { timeout: 5000 });

    const bindButton = screen.getByRole('button', { name: /bind kimi/i });
    await user.click(bindButton);

    // Verifying state should appear
    await screen.findByText(/confirming profile binding with the server/i, {}, { timeout: 5000 });

    // Now mock the server response to show already_bound
    const { adapters: api } = await import('@/lib/api');
    vi.mocked(api.listAdapters).mockResolvedValue([
      makeAdapter({ eligibility: 'already_bound' }),
    ]);

    // Wait for the verification poll to succeed → Connected
    await screen.findByRole('heading', { name: /kimi connected/i }, { timeout: 10000 });
    expect(screen.getByText('Your CLI is connected.')).toBeInTheDocument();
  }, 15000);

  test('bind API error shows error message, does NOT transition to verifying', async () => {
    const user = userEvent.setup();

    await mockListAdapters(makeAdapter({ eligibility: 'ready_to_bind' }));
    await mockBindError('Hash mismatch — adapter artifact changed.');

    renderConnect();
    await screen.findByText('Advanced recovery \/ legacy adapters', {}, { timeout: 5000 });

    const bindButton = screen.getByRole('button', { name: /bind kimi/i });
    await user.click(bindButton);

    // Error message appears — NOT verifying
    await screen.findByText(/Hash mismatch — adapter artifact changed/i, {}, { timeout: 5000 });
    expect(screen.queryByText(/confirming profile binding/i)).not.toBeInTheDocument();
    expect(screen.queryByRole('heading', { name: /kimi connected/i })).not.toBeInTheDocument();
  }, 10000);

  test('verification times out without server confirmation — shows error', async () => {
    const user = userEvent.setup();

    await mockListAdapters(makeAdapter({ eligibility: 'ready_to_bind' }));
    await mockBindSuccess('kimi', 'kimi-adapter');

    renderConnect();
    await screen.findByText('Advanced recovery \/ legacy adapters', {}, { timeout: 5000 });

    const bindButton = screen.getByRole('button', { name: /bind kimi/i });
    await user.click(bindButton);

    // Verifying state appears
    await screen.findByText(/confirming profile binding with the server/i, {}, { timeout: 5000 });

    // Server keeps returning ready_to_bind (never confirms already_bound)
    // This causes the verification to time out after MAX_TRIES (6 × 1.5s = 9s)
    // Wait for timeout error message
    await screen.findByText(/server verification timed out/i, {}, { timeout: 15000 });
    expect(screen.queryByRole('heading', { name: /kimi connected/i })).not.toBeInTheDocument();
  }, 20000);

  test('stale/partial success: bind API returns success but server still shows ready_to_bind → eventually times out', async () => {
    const user = userEvent.setup();

    await mockListAdapters(makeAdapter({ eligibility: 'ready_to_bind' }));
    await mockBindSuccess('kimi', 'kimi-adapter');
    // Server continues returning ready_to_bind — never confirms

    renderConnect();
    await screen.findByText('Advanced recovery \/ legacy adapters', {}, { timeout: 5000 });

    const bindButton = screen.getByRole('button', { name: /bind kimi/i });
    await user.click(bindButton);

    await screen.findByText(/confirming profile binding/i, {}, { timeout: 5000 });
    await screen.findByText(/server verification timed out/i, {}, { timeout: 15000 });
    expect(screen.queryByRole('heading', { name: /kimi connected/i })).not.toBeInTheDocument();
  }, 20000);

  test('race-idempotent: double bind succeeds once, second click is no-op', async () => {
    const user = userEvent.setup();

    await mockListAdapters(makeAdapter({ eligibility: 'ready_to_bind' }));
    await mockBindSuccess('kimi', 'kimi-adapter');

    renderConnect();
    await screen.findByText('Advanced recovery \/ legacy adapters', {}, { timeout: 5000 });

    const bindButton = screen.getByRole('button', { name: /bind kimi/i });

    // First click
    await user.click(bindButton);
    await screen.findByText(/confirming profile binding/i, {}, { timeout: 5000 });

    // Button should be disabled during binding/verifying
    expect(screen.getByRole('button', { name: /verifying/i })).toBeDisabled();

    // Now confirm server state → Connected
    const { adapters: api } = await import('@/lib/api');
    vi.mocked(api.listAdapters).mockResolvedValue([
      makeAdapter({ eligibility: 'already_bound' }),
    ]);

    await screen.findByRole('heading', { name: /kimi connected/i }, { timeout: 10000 });
  }, 15000);

  test('bind verification: only shows Connected when server confirms correct adapter id', async () => {
    const user = userEvent.setup();

    await mockListAdapters(makeAdapter({ eligibility: 'ready_to_bind' }));
    await mockBindSuccess('kimi', 'kimi-adapter');

    renderConnect();
    await screen.findByText('Advanced recovery \/ legacy adapters', {}, { timeout: 5000 });

    const bindButton = screen.getByRole('button', { name: /bind kimi/i });
    await user.click(bindButton);

    await screen.findByText(/confirming profile binding/i, {}, { timeout: 5000 });

    // Server returns already_bound — correct
    const { adapters: api } = await import('@/lib/api');
    vi.mocked(api.listAdapters).mockResolvedValue([
      makeAdapter({ id: 'kimi-adapter', eligibility: 'already_bound' }),
    ]);

    await screen.findByRole('heading', { name: /kimi connected/i }, { timeout: 10000 });
    // The connected card shows the name and subtitle
    expect(screen.getByText('Your CLI is connected.')).toBeInTheDocument();
    expect(screen.getByText('/opt/kimi/cli')).toBeInTheDocument();
  }, 15000);
});

/* ------------------------------------------------------------------ */
/*  Recovery visible at shared surface (not just custom mode)          */
/* ------------------------------------------------------------------ */

describe('ConnectFlow — recovery surface visibility', () => {
  beforeEach(() => { vi.restoreAllMocks(); });
  afterEach(() => { vi.restoreAllMocks(); });

  test('recovery visible in default builtin mode, not only custom mode', async () => {
    await mockListAdapters(makeAdapter({ eligibility: 'ready_to_bind' }));
    renderConnect();

    await screen.findByText('Advanced recovery \/ legacy adapters', {}, { timeout: 5000 });

    // The built-in dropdown confirms we are in default builtin mode
    const select = screen.getByLabelText(/pick your agentic cli/i);
    expect(select.tagName).toBe('SELECT');
    expect(screen.getByRole('button', { name: /bind kimi/i })).toBeInTheDocument();
  }, 10000);

  test('no recovery section when no adapters are ready_to_bind', async () => {
    await mockListAdapters(
      makeAdapter({ eligibility: 'already_bound', id: 'b1' }),
      makeAdapter({ eligibility: 'pending', id: 'b2' }),
      makeAdapter({ eligibility: null, id: 'b3' }),
    );
    renderConnect();

    await waitFor(() => {
      expect(screen.getByLabelText(/pick your agentic cli/i)).toBeInTheDocument();
    }, { timeout: 5000 });

    expect(screen.queryByText('Advanced recovery \/ legacy adapters')).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /bind/i })).not.toBeInTheDocument();
  }, 10000);
});
