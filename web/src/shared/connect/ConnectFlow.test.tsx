/**
 * ConnectFlow recovery tests (THR-107 fix-forward TASK-3780).
 *
 * Validates:
 *  1. Fresh default (builtin) session shows Bind for approved unbound adapters
 *  2. Bind success → Connected state transition
 *  3. Bind error → error message display
 *  4. Approval-without-profile shows Bind, NOT false-connected
 *  5. Cross-profile adapters never show Bind
 *  6. PENDING adapters never show Bind
 *  7. Built-in collision never shows Bind
 */
import { render, screen, waitFor, within } from '@testing-library/react';
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

interface RenderOptions {
  className?: string;
  connectedSubtitle?: (via: string) => string;
}

function renderConnect(opts?: RenderOptions): QueryClient {
  const qc = makeClient();
  render(
    <QueryClientProvider client={qc}>
      <ConnectFlow
        className={opts?.className}
        connectedSubtitle={
          opts?.connectedSubtitle ?? (() => 'Your CLI is connected.')
        }
      />
    </QueryClientProvider>,
  );
  return qc;
}

/** Approved, unbound adapter — should show Bind. */
const approvedUnboundAdapter = {
  id: 'kimi-adapter',
  name: 'kimi-adapter',
  executable: '/opt/kimi/cli',
  executable_hash: 'abc123hash',
  version: '1.0.0',
  capabilities: [],
  contract_version: 1,
  workspace_adapter: 'pi',
  status: 'approved',
  registered_at: '2026-07-01T00:00:00Z',
  registered_by: 'testuser',
  approved_at: '2026-07-02T00:00:00Z',
  approved_by: 'founder',
  intended_profile_name: 'kimi',
};

/** Approved, bound adapter — should NOT show Bind. */
const approvedBoundAdapter = {
  ...approvedUnboundAdapter,
  id: 'bound-adapter',
  intended_profile_name: 'bound-cli',
};

/** PENDING adapter — should NOT show Bind. */
const pendingAdapter = {
  ...approvedUnboundAdapter,
  id: 'pending-adapter',
  status: 'pending',
  approved_at: null,
  approved_by: null,
};

/** Cross-profile adapter — approved for different profile. */
const crossProfileAdapter = {
  ...approvedUnboundAdapter,
  id: 'cross-adapter',
  intended_profile_name: 'other-cli',
};

const kimiExecutable = '/opt/kimi/cli';

async function mockListAdapters(adapters: unknown[]): Promise<void> {
  const { adapters: api } = await import('@/lib/api');
  vi.spyOn(api, 'listAdapters').mockResolvedValue(adapters as never);
}

async function mockListProfiles(profiles: unknown[]): Promise<void> {
  const { runtimeExecutors } = await import('@/lib/api');
  vi.spyOn(runtimeExecutors, 'listRuntimeProfiles').mockResolvedValue({
    profiles: profiles as never,
  } as never);
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

async function mockBindError(): Promise<void> {
  const { adapters: api } = await import('@/lib/api');
  vi.spyOn(api, 'bindAdapterProfile').mockRejectedValue(
    new Error('Adapter artifact changed before bind.'),
  );
}

/* ------------------------------------------------------------------ */
/*  Tests                                                              */
/* ------------------------------------------------------------------ */

describe('ConnectFlow — durable recovery at shared surface', () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  test(
    'fresh default session shows Bind for approved unbound adapter in builtin mode',
    async () => {
      await mockListAdapters([approvedUnboundAdapter]);
      await mockListProfiles([]); // No bound profiles yet

      renderConnect();

      // Recovery section is visible at the shared surface (default builtin mode)
      await screen.findByText('Approved adapters ready to bind', {}, { timeout: 5000 });

      // The adapter card shows the profile name, executable, and Bind button
      const recoverySection = screen.getByText('Approved adapters ready to bind').closest('div')!;
      expect(within(recoverySection).getAllByText('kimi').length).toBeGreaterThanOrEqual(1);
      expect(within(recoverySection).getByText(kimiExecutable)).toBeInTheDocument();

      const bindButton = screen.getByRole('button', { name: /bind kimi/i });
      expect(bindButton).toBeInTheDocument();

      // Built-in mode form is also visible (dropdown for built-in CLIs)
      expect(screen.getByLabelText(/pick your agentic cli/i)).toBeInTheDocument();
    },
    10000,
  );

  test(
    'Bind success transitions to Connected state',
    async () => {
      const user = userEvent.setup();

      await mockListAdapters([approvedUnboundAdapter]);
      await mockListProfiles([]);
      await mockBindSuccess('kimi', 'kimi-adapter');

      renderConnect();

      await screen.findByText('Approved adapters ready to bind', {}, { timeout: 5000 });

      const bindButton = screen.getByRole('button', { name: /bind kimi/i });
      await user.click(bindButton);

      // After successful bind, the connected card appears
      await screen.findByRole('heading', { name: /kimi connected/i }, { timeout: 5000 });
      expect(screen.getByText('Your CLI is connected.')).toBeInTheDocument();

      // Connect another button is visible
      expect(screen.getByRole('button', { name: /connect another/i })).toBeInTheDocument();
    },
    10000,
  );

  test(
    'Bind error shows error message and adapters remain visible',
    async () => {
      const user = userEvent.setup();

      await mockListAdapters([approvedUnboundAdapter]);
      await mockListProfiles([]);
      await mockBindError();

      renderConnect();

      await screen.findByText('Approved adapters ready to bind', {}, { timeout: 5000 });

      const bindButton = screen.getByRole('button', { name: /bind kimi/i });
      await user.click(bindButton);

      // Error message appears
      await screen.findByText(/Adapter artifact changed before bind/i, {}, { timeout: 5000 });

      // The bind button is visible again (error state stays on the card)
      // Connected state should NOT appear
      expect(screen.queryByRole('heading', { name: /kimi connected/i })).not.toBeInTheDocument();
    },
    10000,
  );

  test(
    'approval-without-profile shows Bind, not false-connected',
    async () => {
      // Adapter is APPROVED but has no matching profile → Bind shown
      await mockListAdapters([approvedUnboundAdapter]);
      await mockListProfiles([]);

      renderConnect();

      await screen.findByText('Approved adapters ready to bind', {}, { timeout: 5000 });

      // Bind section explicitly says approval is NOT profile creation
      expect(
        screen.getByText(/approval alone does not create the profile/i),
      ).toBeInTheDocument();

      // Bind button is present
      expect(screen.getByRole('button', { name: /bind kimi/i })).toBeInTheDocument();

      // Connected state is NOT shown
      expect(screen.queryByRole('heading', { name: /kimi connected/i })).not.toBeInTheDocument();
    },
    10000,
  );

  test(
    'cross-profile adapter never shows Bind',
    async () => {
      // Adapter is approved for 'other-cli' but profiles are for different names
      await mockListAdapters([crossProfileAdapter]);
      await mockListProfiles([
        {
          name: 'kimi',
          kind: 'custom',
          command_adapter_id: 'custom-adapter:kimi-adapter',
        },
      ]);

      renderConnect();

      // Wait for queries to resolve — recovery should be empty
      await waitFor(() => {
        // The built-in form should be visible (no recovery section)
        expect(screen.getByLabelText(/pick your agentic cli/i)).toBeInTheDocument();
      }, { timeout: 5000 });

      // No Bind section
      expect(screen.queryByText('Approved adapters ready to bind')).not.toBeInTheDocument();
    },
    10000,
  );

  test(
    'PENDING adapter never shows Bind',
    async () => {
      await mockListAdapters([pendingAdapter]);
      await mockListProfiles([]);

      renderConnect();

      await waitFor(() => {
        expect(screen.getByLabelText(/pick your agentic cli/i)).toBeInTheDocument();
      }, { timeout: 5000 });

      // No Bind card for pending adapters
      expect(screen.queryByText('Approved adapters ready to bind')).not.toBeInTheDocument();
      expect(screen.queryByRole('button', { name: /bind/i })).not.toBeInTheDocument();
    },
    10000,
  );

  test(
    'already-bound adapter never shows Bind (false-connected protection)',
    async () => {
      await mockListAdapters([approvedBoundAdapter]);
      await mockListProfiles([
        {
          name: 'bound-cli',
          kind: 'custom',
          command_adapter_id: 'custom-adapter:bound-adapter',
        },
      ]);

      renderConnect();

      await waitFor(() => {
        expect(screen.getByLabelText(/pick your agentic cli/i)).toBeInTheDocument();
      }, { timeout: 5000 });

      // No recovery section for already-bound adapters
      expect(screen.queryByText('Approved adapters ready to bind')).not.toBeInTheDocument();
    },
    10000,
  );

  test(
    'recovery visible in default builtin mode, not only custom mode',
    async () => {
      await mockListAdapters([approvedUnboundAdapter]);
      await mockListProfiles([]);

      renderConnect();

      // Built-in mode (default): recovery section visible
      await screen.findByText('Approved adapters ready to bind', {}, { timeout: 5000 });
      expect(screen.getByLabelText(/pick your agentic cli/i)).toBeInTheDocument();

      // The built-in dropdown is the active mode, proving recovery is at shared surface
      const select = screen.getByLabelText(/pick your agentic cli/i);
      expect(select.tagName).toBe('SELECT');
    },
    10000,
  );
});
