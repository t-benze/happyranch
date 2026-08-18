/**
 * ConnectFlow direct-connect tests (THR-107 slice 3 + workspace_adapter_id
 * self-declaration follow-up).
 *
 * Validates the instant Connect -> Connected flow:
 *  1. Mint requires a valid, non-built-in name -- and nothing else. There
 *     is no workspace-CLI dropdown: the connecting wrapper declares its own
 *     workspace_adapter_id in its /connect manifest, not the founder.
 *  2. Waiting state shows the daemon-issued wrapper path from GET status,
 *     and the generated prompt guides the wrapper author to declare
 *     workspace_adapter_id themselves.
 *  3. A polled terminal status transitions to Connected or a retryable error;
 *     the browser never automatically calls commit.
 *  5. No pending-approval / recovery-bind UI exists anywhere in the flow
 *     (that surface was deleted in this slice).
 */
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { describe, test, expect, vi, beforeEach, afterEach } from 'vitest';
import { ConnectFlow, FailedConnectionClearedBody } from './ConnectFlow';

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
      <ConnectFlow connectedSubtitle={() => 'Your CLI is connected.'} />
    </QueryClientProvider>,
  );
  return qc;
}

async function goCustomAdapter(user: ReturnType<typeof userEvent.setup>): Promise<void> {
  await user.click(await screen.findByRole('button', { name: /connect a custom cli instead/i }));
  expect(await screen.findByText(/create a custom adapter wrapper/i)).toBeInTheDocument();
}

async function mockMint(token = 'hrreg_test') {
  const { settings: api } = await import('@/lib/api');
  return vi.spyOn(api, 'mintRuntimeRegistrationToken').mockResolvedValue({
    token,
    expires_at: Date.now() / 1000 + 1800,
  });
}

async function mockStatus(
  impl: (name: string) => import('@/lib/api/directConnect').DirectConnectStatus,
): Promise<void> {
  const { directConnect: api } = await import('@/lib/api');
  vi.spyOn(api, 'getStatus').mockImplementation(async (name: string) => impl(name));
}

/* ------------------------------------------------------------------ */
/*  Tests                                                              */
/* ------------------------------------------------------------------ */

describe('ConnectFlow — direct connect (THR-107 slice 3)', () => {
  beforeEach(() => { vi.restoreAllMocks(); });
  afterEach(() => { vi.restoreAllMocks(); });

  test('form requires only a valid non-built-in name -- no workspace-CLI field exists', async () => {
    const user = userEvent.setup();
    renderConnect();
    await goCustomAdapter(user);

    expect(screen.queryByLabelText(/workspace cli/i)).not.toBeInTheDocument();

    const gen = screen.getByRole('button', { name: /generate connect prompt/i });
    expect(gen).toBeDisabled();

    await user.type(screen.getByLabelText(/name this cli/i), 'my-cli');
    expect(gen).not.toBeDisabled();
  });

  test('built-in name is refused', async () => {
    const user = userEvent.setup();
    renderConnect();
    await goCustomAdapter(user);

    await user.type(screen.getByLabelText(/name this cli/i), 'codex');

    expect(screen.getByRole('button', { name: /generate connect prompt/i })).toBeDisabled();
    expect(screen.getByText(/isn.*t a built-in/i)).toBeInTheDocument();
  });

  test('waiting state shows the daemon-issued wrapper path, no PENDING wording, and provider-neutral direct-conformance guidance', async () => {
    const user = userEvent.setup();
    const mintSpy = await mockMint();
    await mockStatus(() => ({
      wrapper_destination: '/tmp/happyranch-daemon/adapters/my-cli-adapter',
      operation_id: null,
      profile_state: null,
      reason: null,
    }));

    renderConnect();
    await goCustomAdapter(user);
    await user.type(screen.getByLabelText(/name this cli/i), 'my-cli');
    await user.click(screen.getByRole('button', { name: /generate connect prompt/i }));

    await screen.findByLabelText(/waiting for adapter submission/i);

    // The mint request never carries a founder-chosen workspace_adapter_id
    // choice -- only the fixed internal activation trigger.
    expect(mintSpy).toHaveBeenCalledWith(
      expect.objectContaining({ name: 'my-cli', purpose: 'adapter', intended_profile_name: 'my-cli' }),
    );

    const promptText = document.querySelector('pre')?.textContent ?? '';
    expect(promptText).toContain('/tmp/happyranch-daemon/adapters/my-cli-adapter');
    expect(promptText).not.toContain('PENDING');
    expect(screen.queryByText(/awaiting approval/i)).not.toBeInTheDocument();
    // The prompt guides the wrapper author to pick their own convention and
    // send it in the POST body -- not something the founder chose upstream.
    expect(promptText).toContain('WORKSPACE_ADAPTER_ID');
    expect(promptText).toContain('workspace_adapter_id');
    expect(promptText).toContain('YOUR CLI\'s expectations');
    expect(promptText).toContain('ENTIRE AdapterInput.prompt');
    expect(promptText).toContain('one real');
    expect(promptText).toContain('provider invocation');
    expect(promptText).toContain('genuine terminal provider response');
    expect(promptText).toContain('wrapper, not the provider, constructs the AdapterOutput');
    expect(promptText).toContain('complete opaque canary');
    expect(promptText).toContain('Do not use optional tools or explore the workspace');
    expect(promptText).toContain('normal task behavior is unchanged');
  });

  test('a stale removed committed projection stays in the waiting copy-prompt state', async () => {
    const user = userEvent.setup();
    await mockMint();
    await mockStatus(() => ({
      wrapper_destination: '/tmp/happyranch-daemon/adapters/my-cli-adapter',
      operation_id: 'stale-id',
      profile_state: null,
      reason: null,
    }));

    renderConnect();
    await goCustomAdapter(user);
    await user.type(screen.getByLabelText(/name this cli/i), 'my-cli');
    await user.click(screen.getByRole('button', { name: /generate connect prompt/i }));

    await expect(
      screen.findByText(/finishing connection/i, {}, { timeout: 200 }),
    ).rejects.toThrow();
    expect(screen.getByLabelText(/waiting for adapter submission/i)).toBeInTheDocument();
    expect(screen.queryByText(/finishing connection/i)).not.toBeInTheDocument();
  });

  test('a polled committed status renders Connected without a browser commit', async () => {
    const user = userEvent.setup();
    await mockMint();
    let landed = false;
    await mockStatus(() => ({
      wrapper_destination: '/tmp/happyranch-daemon/adapters/my-cli-adapter',
      operation_id: landed ? 'op-1' : null,
      profile_state: landed ? 'committed' : null,
      reason: null,
    }));
    const { directConnect: api } = await import('@/lib/api');
    const commitSpy = vi.spyOn(api, 'commit');

    renderConnect();
    await goCustomAdapter(user);
    await user.type(screen.getByLabelText(/name this cli/i), 'my-cli');
    await user.click(screen.getByRole('button', { name: /generate connect prompt/i }));
    await screen.findByLabelText(/waiting for adapter submission/i);

    landed = true;

    await screen.findByRole('heading', { name: /my-cli connected/i }, { timeout: 10000 });
    expect(screen.getByText('Your CLI is connected.')).toBeInTheDocument();
    expect(commitSpy).not.toHaveBeenCalled();
  }, 15000);

  test('a polled failed status shows a retryable error, never a false Connected card', async () => {
    const user = userEvent.setup();
    await mockMint();
    await mockStatus(() => ({
      wrapper_destination: '/tmp/happyranch-daemon/adapters/my-cli-adapter',
      operation_id: 'op-1',
      profile_state: 'failed',
      reason: 'conformance_probe_failed: boom',
    }));
    const { directConnect: api } = await import('@/lib/api');
    const commitSpy = vi.spyOn(api, 'commit');

    renderConnect();
    await goCustomAdapter(user);
    await user.type(screen.getByLabelText(/name this cli/i), 'my-cli');
    await user.click(screen.getByRole('button', { name: /generate connect prompt/i }));

    await screen.findByText(/connection failed/i, {}, { timeout: 10000 });
    expect(screen.getByText(/boom/i)).toBeInTheDocument();
    expect(screen.queryByRole('heading', { name: /connected/i })).not.toBeInTheDocument();
    expect(commitSpy).not.toHaveBeenCalled();
  }, 15000);

  test('a failed connection clears only after confirmation and offers reconnect with the wrapper result', async () => {
    const user = userEvent.setup();
    await mockMint();
    await mockStatus(() => ({
      wrapper_destination: '/tmp/happyranch-daemon/adapters/my-cli-adapter',
      operation_id: 'op-1', profile_state: 'failed', reason: 'terminal failure',
    }));
    const { directConnect: api } = await import('@/lib/api');
    const forgetSpy = vi.spyOn(api, 'forget').mockResolvedValue({
      operation_id: 'op-1', status: 'forgotten', wrapper_status: 'preserved_changed',
    });

    renderConnect();
    await goCustomAdapter(user);
    await user.type(screen.getByLabelText(/name this cli/i), 'my-cli');
    await user.click(screen.getByRole('button', { name: /generate connect prompt/i }));
    await screen.findByText(/connection failed/i, {}, { timeout: 10000 });

    await user.click(screen.getByRole('button', { name: /clear failed connection/i }));
    expect(forgetSpy).not.toHaveBeenCalled();
    await user.click(screen.getByRole('button', { name: /confirm clear failed connection/i }));

    await screen.findByText(/failed connection cleared/i);
    expect(forgetSpy).toHaveBeenCalledWith('op-1');
    expect(screen.getByText(/preserved because it changed/i)).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: /reconnect this cli/i }));
    expect(await screen.findByText(/create a custom adapter wrapper/i)).toBeInTheDocument();
  }, 15000);

  test('a server refusal keeps the failed result visible without claiming it cleared', async () => {
    const user = userEvent.setup();
    await mockMint();
    await mockStatus(() => ({
      wrapper_destination: '/tmp/happyranch-daemon/adapters/my-cli-adapter',
      operation_id: 'op-1', profile_state: 'failed', reason: 'terminal failure',
    }));
    const { directConnect: api } = await import('@/lib/api');
    const { ApiError } = await import('@/lib/api');
    vi.spyOn(api, 'forget').mockRejectedValue(new ApiError(409, null, 'refused: retry validation is running'));

    renderConnect();
    await goCustomAdapter(user);
    await user.type(screen.getByLabelText(/name this cli/i), 'my-cli');
    await user.click(screen.getByRole('button', { name: /generate connect prompt/i }));
    await screen.findByText(/connection failed/i, {}, { timeout: 10000 });
    await user.click(screen.getByRole('button', { name: /clear failed connection/i }));
    await user.click(screen.getByRole('button', { name: /confirm clear failed connection/i }));

    expect(await screen.findByRole('alert')).toHaveTextContent('refused: retry validation is running');
    expect(screen.getByText(/connection failed/i)).toBeInTheDocument();
    expect(screen.queryByText(/failed connection cleared/i)).not.toBeInTheDocument();
  }, 15000);

  test('clear shows a submitting disabled state until the server returns', async () => {
    const user = userEvent.setup();
    await mockMint();
    await mockStatus(() => ({
      wrapper_destination: '/tmp/happyranch-daemon/adapters/my-cli-adapter',
      operation_id: 'op-1', profile_state: 'failed', reason: 'terminal failure',
    }));
    let resolveForget: (value: { operation_id: string; status: 'forgotten'; wrapper_status: 'removed' }) => void;
    const pendingForget = new Promise<{ operation_id: string; status: 'forgotten'; wrapper_status: 'removed' }>((resolve) => {
      resolveForget = resolve;
    });
    const { directConnect: api } = await import('@/lib/api');
    vi.spyOn(api, 'forget').mockReturnValue(pendingForget);

    renderConnect();
    await goCustomAdapter(user);
    await user.type(screen.getByLabelText(/name this cli/i), 'my-cli');
    await user.click(screen.getByRole('button', { name: /generate connect prompt/i }));
    await screen.findByText(/connection failed/i, {}, { timeout: 10000 });
    await user.click(screen.getByRole('button', { name: /clear failed connection/i }));
    await user.click(screen.getByRole('button', { name: /confirm clear failed connection/i }));

    expect(screen.getByRole('button', { name: /clearing/i })).toBeDisabled();
    expect(screen.getByRole('button', { name: /^retry$/i })).toBeDisabled();
    resolveForget!({ operation_id: 'op-1', status: 'forgotten', wrapper_status: 'removed' });
    expect(await screen.findByText(/matching failed wrapper was removed/i)).toBeInTheDocument();
  }, 15000);

  test.each([
    ['removed', /matching failed wrapper was removed/i],
    ['already_absent', /failed wrapper was already absent/i],
    ['preserved_changed', /preserved because it changed/i],
    ['preserved_unsafe', /could not safely prove it matched/i],
  ] as const)('reports the %s wrapper cleanup result', (wrapperStatus, message) => {
    render(
      <FailedConnectionClearedBody
        name="my-cli"
        wrapperStatus={wrapperStatus}
        onReconnect={vi.fn()}
      />,
    );
    expect(screen.getByText(message)).toBeInTheDocument();
  });

  test('a planned status keeps finishing the connection until a later committed poll', async () => {
    const user = userEvent.setup();
    await mockMint();
    let landed = false;
    let committed = false;
    await mockStatus(() => ({
      wrapper_destination: '/tmp/happyranch-daemon/adapters/my-cli-adapter',
      operation_id: landed ? 'op-1' : null,
      profile_state: landed ? (committed ? 'committed' : 'planned') : null,
      reason: null,
    }));
    const { directConnect: api } = await import('@/lib/api');
    const commitSpy = vi.spyOn(api, 'commit');

    renderConnect();
    await goCustomAdapter(user);
    await user.type(screen.getByLabelText(/name this cli/i), 'my-cli');
    await user.click(screen.getByRole('button', { name: /generate connect prompt/i }));
    await screen.findByLabelText(/waiting for adapter submission/i);

    landed = true;
    await screen.findByText(/finishing connection/i, {}, { timeout: 10000 });
    expect(screen.queryByRole('heading', { name: /connected/i })).not.toBeInTheDocument();

    committed = true;
    await screen.findByRole('heading', { name: /my-cli connected/i }, { timeout: 10000 });
    expect(commitSpy).not.toHaveBeenCalled();
  }, 15000);

  test('retry after a failed projection uses retry validation and can reach Connected', async () => {
    const user = userEvent.setup();
    await mockMint();
    await mockStatus(() => ({
      wrapper_destination: '/tmp/happyranch-daemon/adapters/my-cli-adapter',
      operation_id: 'op-1',
      profile_state: 'failed',
      reason: 'initial projection failure',
    }));
    const { directConnect: api } = await import('@/lib/api');
    const retrySpy = vi.spyOn(api, 'retry')
      .mockResolvedValueOnce({ operation_id: 'op-1', profile_state: 'failed', reason: 'transient error' });

    renderConnect();
    await goCustomAdapter(user);
    await user.type(screen.getByLabelText(/name this cli/i), 'my-cli');
    await user.click(screen.getByRole('button', { name: /generate connect prompt/i }));
    await screen.findByText(/connection failed/i, {}, { timeout: 10000 });
    expect(retrySpy).not.toHaveBeenCalled();

    retrySpy.mockResolvedValueOnce({ operation_id: 'op-1', profile_state: 'committed', profile_name: 'my-cli' });
    await user.click(screen.getByRole('button', { name: /^retry$/i }));

    await screen.findByText(/transient error/i, {}, { timeout: 10000 });
    await user.click(screen.getByRole('button', { name: /^retry$/i }));

    await screen.findByRole('heading', { name: /my-cli connected/i }, { timeout: 10000 });
    expect(retrySpy).toHaveBeenCalledTimes(2);
    expect(vi.spyOn(api, 'commit')).not.toHaveBeenCalled();
  }, 15000);

  test('a planned direct commit response stays in progress until status commits', async () => {
    const user = userEvent.setup();
    await mockMint();
    let profileState: 'planned' | 'committed' | 'failed' = 'failed';
    await mockStatus(() => ({
      wrapper_destination: '/tmp/happyranch-daemon/adapters/my-cli-adapter',
      operation_id: 'op-1',
      profile_state: profileState,
      reason: profileState === 'failed' ? 'initial projection failure' : null,
    }));
    const { directConnect: api } = await import('@/lib/api');
    const retrySpy = vi.spyOn(api, 'retry').mockImplementation(async () => {
      profileState = 'planned';
      return { operation_id: 'op-1', profile_state: 'planned' };
    });

    renderConnect();
    await goCustomAdapter(user);
    await user.type(screen.getByLabelText(/name this cli/i), 'my-cli');
    await user.click(screen.getByRole('button', { name: /generate connect prompt/i }));
    await screen.findByText(/connection failed/i, {}, { timeout: 10000 });

    await user.click(screen.getByRole('button', { name: /^retry$/i }));
    await screen.findByText(/finishing connection/i, {}, { timeout: 10000 });
    expect(screen.queryByText(/connection failed/i)).not.toBeInTheDocument();
    expect(retrySpy).toHaveBeenCalledTimes(1);

    profileState = 'committed';
    await screen.findByRole('heading', { name: /my-cli connected/i }, { timeout: 10000 });
    expect(retrySpy).toHaveBeenCalledTimes(1);
  }, 20000);

  test('a planned direct commit response still renders a terminal failed status', async () => {
    const user = userEvent.setup();
    await mockMint();
    let profileState: 'planned' | 'committed' | 'failed' = 'failed';
    await mockStatus(() => ({
      wrapper_destination: '/tmp/happyranch-daemon/adapters/my-cli-adapter',
      operation_id: 'op-1',
      profile_state: profileState,
      reason: profileState === 'failed' ? 'conformance_probe_failed: terminal boom' : null,
    }));
    const { directConnect: api } = await import('@/lib/api');
    const retrySpy = vi.spyOn(api, 'retry').mockImplementation(async () => {
      profileState = 'planned';
      return { operation_id: 'op-1', profile_state: 'planned' };
    });

    renderConnect();
    await goCustomAdapter(user);
    await user.type(screen.getByLabelText(/name this cli/i), 'my-cli');
    await user.click(screen.getByRole('button', { name: /generate connect prompt/i }));
    await screen.findByText(/connection failed/i, {}, { timeout: 10000 });

    await user.click(screen.getByRole('button', { name: /^retry$/i }));
    await screen.findByText(/finishing connection/i, {}, { timeout: 10000 });
    expect(screen.queryByText(/connection failed/i)).not.toBeInTheDocument();
    expect(retrySpy).toHaveBeenCalledTimes(1);

    profileState = 'failed';
    await screen.findByText(/terminal boom/i, {}, { timeout: 10000 });
    expect(screen.queryByRole('heading', { name: /connected/i })).not.toBeInTheDocument();
    expect(retrySpy).toHaveBeenCalledTimes(1);
  }, 20000);

  test('no pending-approval or recovery-bind UI exists anywhere in the flow', async () => {
    const user = userEvent.setup();
    renderConnect();

    // Default (builtin) mode.
    expect(screen.queryByText(/advanced recovery/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/pending/i)).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /^bind /i })).not.toBeInTheDocument();

    await goCustomAdapter(user);
    expect(screen.queryByText(/awaiting approval/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/founder must approve/i)).not.toBeInTheDocument();
  });
});
