import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, test, vi } from 'vitest';
import { ApiError } from '@/lib/api';
import { DaemonCapacitySection } from './DaemonCapacitySection';

const hooks = vi.hoisted(() => ({ query: vi.fn(), mutation: vi.fn() }));
vi.mock('@/hooks/settings', () => ({
  useDaemonCapacity: hooks.query,
  useUpdateDaemonCapacity: hooks.mutation,
}));

const snapshot = {
  running_at_daemon_start: { queue_workers: 6, host_global_session_cap: 13 },
  running_provenance: 'startup-resolved settings snapshot',
  persisted_yaml: { queue_workers: null, host_global_session_cap: null },
  next_start: { queue_workers: 6, host_global_session_cap: 13 },
  environment_shadowed: [] as string[], environment_warning: null as string | null,
  effective_admission_reason: 'startup policy', revision: `sha256:${'a'.repeat(64)}`,
  restart_required: false, restart_pending: false,
  guidance: { queue_workers: 'Empirical workers', host_global_session_cap: 'Empirical cap', enforced: false },
  authorization: 'daemon bearer required',
};
const mutateAsync = vi.fn();

function loaded(overrides = {}) {
  hooks.query.mockReturnValue({ data: { ...snapshot, ...overrides }, isLoading: false, isError: false });
  hooks.mutation.mockReturnValue({ mutateAsync, isPending: false });
}

async function fillAndSave() {
  const user = userEvent.setup();
  await user.type(screen.getByLabelText('Rationale'), 'measured receipts');
  await user.click(screen.getByRole('button', { name: 'Save for next restart' }));
}

describe('DaemonCapacitySection readiness matrix', () => {
  beforeEach(() => { vi.clearAllMocks(); loaded(); });

  test('loading does not flash defaults or controls', () => {
    hooks.query.mockReturnValue({ isLoading: true, isError: false });
    render(<DaemonCapacitySection />);
    expect(screen.getByRole('status')).toHaveTextContent('Loading');
    expect(screen.queryByRole('button')).not.toBeInTheDocument();
  });

  test('load/parse/storage failure is safe and read-only', () => {
    hooks.query.mockReturnValue({ isLoading: false, isError: true, error: new ApiError(500, 'config_parse_failed', {}) });
    render(<DaemonCapacitySection />);
    expect(screen.getByRole('alert')).toHaveTextContent('No values are displayed');
    expect(screen.queryByRole('button')).not.toBeInTheDocument();
  });

  test('no-file/empty and populated current values render distinctly', () => {
    const { rerender } = render(<DaemonCapacitySection />);
    expect(screen.getByText('Not set / Not set')).toBeInTheDocument();
    loaded({ persisted_yaml: { queue_workers: 4, host_global_session_cap: 11 } });
    rerender(<DaemonCapacitySection />);
    expect(screen.getByText('4 / 11')).toBeInTheDocument();
  });

  test('local validation retains draft and focuses announced error', async () => {
    render(<DaemonCapacitySection />);
    const user = userEvent.setup();
    await user.clear(screen.getByLabelText('Concurrent task sessions'));
    await user.type(screen.getByLabelText('Concurrent task sessions'), '0');
    fireEvent.submit(screen.getByRole('button').closest('form')!);
    expect(await screen.findByText(/Enter positive whole numbers/)).toBeInTheDocument();
    await waitFor(() => expect(document.activeElement).toHaveTextContent('positive whole numbers'));
    expect(screen.getByLabelText('Concurrent task sessions')).toHaveValue(0);
    expect(mutateAsync).not.toHaveBeenCalled();
  });

  test('environment shadow blocks save until explicit confirmation', async () => {
    loaded({ environment_shadowed: ['queue_workers'], environment_warning: 'Environment overrides win; restart alone will not make YAML win.' });
    render(<DaemonCapacitySection />);
    expect(screen.getByRole('button')).toBeDisabled();
    await userEvent.click(screen.getByRole('checkbox'));
    expect(screen.getByRole('button')).toBeEnabled();
  });

  test('saving state disables duplicate submission and uses truthful copy', () => {
    hooks.mutation.mockReturnValue({ mutateAsync, isPending: true });
    render(<DaemonCapacitySection />);
    expect(screen.getByRole('button', { name: 'Saving…' })).toBeDisabled();
  });

  test('success announces restart-pending/no-live-apply truth', async () => {
    mutateAsync.mockResolvedValue({ ...snapshot, restart_pending: true,
      message: 'Saved for next daemon restart; no running capacity was changed.' });
    render(<DaemonCapacitySection />);
    await fillAndSave();
    expect(await screen.findByText(/no running capacity was changed/i)).toBeInTheDocument();
  });

  test('stale conflict shows latest safe snapshot while preserving draft', async () => {
    mutateAsync.mockRejectedValue(new ApiError(409, 'stale_revision', {
      latest: { revision: 'sha256:new', persisted_yaml: { queue_workers: 7, host_global_session_cap: 14 } },
    }));
    render(<DaemonCapacitySection />);
    await fillAndSave();
    expect(await screen.findByText(/Latest revision .*7 \/ 14/)).toBeInTheDocument();
    expect(screen.getByLabelText('Rationale')).toHaveValue('measured receipts');
  });

  test.each([
    [new ApiError(401, null, {}), /valid daemon bearer/i],
    [new ApiError(503, 'audit_failed', {}), /Audit storage is unavailable/i],
    [new ApiError(503, 'config_write_failed', {}), /previous authoritative file/i],
    [new ApiError(503, 'config_publication_uncertain', {}), /reload and inspect.*before retrying/i],
  ])('maps safe typed save failure without leaking detail', async (error, message) => {
    mutateAsync.mockRejectedValue(error);
    render(<DaemonCapacitySection />);
    await fillAndSave();
    expect(await screen.findByText(message)).toBeInTheDocument();
    expect(document.body).not.toHaveTextContent('secret');
  });

  test('dirty draft installs a navigation/reload warning', async () => {
    render(<DaemonCapacitySection />);
    await userEvent.type(screen.getByLabelText('Rationale'), 'draft');
    const event = new Event('beforeunload', { cancelable: true });
    window.dispatchEvent(event);
    expect(event.defaultPrevented).toBe(true);
    expect(screen.getByText(/Leaving or reloading will discard/i)).toBeInTheDocument();
  });
});
