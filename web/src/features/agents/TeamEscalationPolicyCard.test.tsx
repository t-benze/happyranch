import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { ApiError } from '@/lib/api';
import { TeamEscalationPolicyCard } from './TeamEscalationPolicyCard';

const template = {
  title: 'Canonical policy',
  normative_text: 'Normative text',
  clauses: [{ id: 'esc-one', category: 'protected', condition: 'Stop.', action: 'escalate_to_founder' as const }],
  continuation_phrase: 'routine same-root follow-through of the already-completed slice',
};
const empty = {
  team: 'engineering' as const, target_manager: 'engineering_manager' as const,
  can_mutate: true as const, bootstrap_required: true as const,
  activation_guard: { ready: false, reason: 'production verification required' },
  bootstrap_template: template,
};
const active = {
  ...empty,
  bootstrap_required: undefined,
  activation_guard: { ready: true, reason: '' },
  active: {
    activation_id: 'APA-active', epoch: 7, action: 'activate' as const,
    created_at: '2026-09-02T00:00:00Z', actor_attribution: 'shared local operator credential' as const,
    release: {
      id: 'APR-active', policy_id: 'APP-engineering', version: 3,
      ...template, digest: '1234567890abcdef', created_at: '2026-09-02T00:00:00Z',
      actor_attribution: 'shared local operator credential' as const,
    },
  },
};
const query = { data: empty as typeof empty | typeof active | undefined, isLoading: false, isError: false, error: null };
const create = { mutateAsync: vi.fn(), isPending: false };
const activate = { mutateAsync: vi.fn(), isPending: false };

vi.mock('@/hooks/authorityPolicy', () => ({
  useTeamEscalationPolicy: () => query,
  useCreateTeamEscalationPolicyRelease: () => create,
  useActivateTeamEscalationPolicyRelease: () => activate,
}));

const agent = { name: 'engineering_manager', team: 'engineering', role: 'manager' };

describe('TeamEscalationPolicyCard', () => {
  beforeEach(() => {
    query.data = empty; query.isLoading = false; query.isError = false; query.error = null;
    create.mutateAsync.mockReset(); activate.mutateAsync.mockReset();
  });

  it('renders deterministic loading and sanitized error states', () => {
    query.isLoading = true;
    const view = render(<TeamEscalationPolicyCard agent={agent} />);
    expect(screen.getByText('Loading team policy…')).toBeInTheDocument();
    query.isLoading = false; query.isError = true;
    view.rerender(<TeamEscalationPolicyCard agent={agent} />);
    expect(screen.getByRole('alert')).toHaveTextContent('Could not load');
  });

  it('labels team ownership, bootstrap and shared-credential limitation', async () => {
    render(<TeamEscalationPolicyCard agent={agent} />);
    expect(await screen.findByText('Team-owned')).toBeInTheDocument();
    expect(screen.getByText(/not by this agent/i)).toBeInTheDocument();
    expect(screen.getByText(/shared local operator credential/i)).toBeInTheDocument();
    expect(screen.getByText(/No active release/)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Save & activate' })).toBeDisabled();
    expect(screen.getByText(/Activation unavailable/)).toHaveTextContent('production verification required');
  });

  it('preserves dirty draft on conflict and saves an immutable inactive version', async () => {
    create.mutateAsync.mockRejectedValueOnce(new ApiError(409, 'base_release_changed', {}));
    render(<TeamEscalationPolicyCard agent={agent} />);
    const title = await screen.findByLabelText('Title');
    fireEvent.change(title, { target: { value: 'Edited policy' } });
    fireEvent.click(screen.getByRole('button', { name: 'Save immutable version' }));
    await waitFor(() => expect(screen.getByText(/active base changed/i)).toBeInTheDocument());
    expect(title).toHaveValue('Edited policy');

    create.mutateAsync.mockResolvedValueOnce({
      release: { id: 'APR-new', version: 2 }, activated: false,
      validation: { canonical: true, digest: 'digest' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Save immutable version' }));
    await waitFor(() => expect(screen.getByText(/Saved inactive v2/)).toBeInTheDocument());
    expect(activate.mutateAsync).not.toHaveBeenCalled();
  });

  it('rejects blank editable policy text before any request', async () => {
    render(<TeamEscalationPolicyCard agent={agent} />);
    fireEvent.change(await screen.findByLabelText('Normative policy'), { target: { value: ' ' } });
    expect(screen.getByRole('status')).toHaveTextContent('Title and normative policy are required.');
    expect(screen.getByRole('button', { name: 'Save immutable version' })).toBeDisabled();
    expect(create.mutateAsync).not.toHaveBeenCalled();
  });

  it('renders an active release and enables actions only for a dirty valid draft', async () => {
    query.data = active;
    render(<TeamEscalationPolicyCard agent={agent} />);
    expect(await screen.findByText(/Active v3 · epoch 7 · 1234567890ab/)).toBeInTheDocument();
    const save = screen.getByRole('button', { name: 'Save immutable version' });
    const saveAndActivate = screen.getByRole('button', { name: 'Save & activate' });
    expect(save).toBeDisabled(); expect(saveAndActivate).toBeDisabled();
    fireEvent.change(screen.getByLabelText('Title'), { target: { value: 'Edited policy' } });
    expect(save).toBeEnabled(); expect(saveAndActivate).toBeEnabled();
    expect(screen.getByRole('button', { name: 'Reactivate older version' })).toHaveAttribute(
      'title', 'Rollback selection and history arrive in S6',
    );
  });

  it.each([
    [new ApiError(422, 'invalid_policy', {}), /server rejected this policy contract/i],
    [new ApiError(500, 'internal_error', {}), /policy could not be saved/i],
  ])('shows the bounded save failure without a receipt', async (error, copy) => {
    create.mutateAsync.mockRejectedValueOnce(error);
    render(<TeamEscalationPolicyCard agent={agent} />);
    fireEvent.change(await screen.findByLabelText('Title'), { target: { value: 'Edited policy' } });
    fireEvent.click(screen.getByRole('button', { name: 'Save immutable version' }));
    expect(await screen.findByText(copy)).toBeInTheDocument();
    expect(screen.queryByText(/Saved inactive v/)).not.toBeInTheDocument();
  });

  it('confirms activation and preserves the exact release/CAS payload', async () => {
    query.data = active;
    create.mutateAsync.mockResolvedValueOnce({
      release: { id: 'APR-new', version: 4 }, activated: false,
      validation: { canonical: true, digest: 'digest' },
    });
    activate.mutateAsync.mockResolvedValueOnce({});
    render(<TeamEscalationPolicyCard agent={agent} />);
    fireEvent.change(await screen.findByLabelText('Title'), { target: { value: 'Edited policy' } });
    fireEvent.click(screen.getByRole('button', { name: 'Save & activate' }));
    expect(screen.getByRole('dialog', { name: 'activate policy confirmation' })).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Confirm' }));
    await waitFor(() => expect(activate.mutateAsync).toHaveBeenCalledWith({
      agentName: 'engineering_manager',
      body: {
        release_id: 'APR-new', expected_previous_epoch: 7, request_id: expect.any(String),
        action: 'activate', acknowledge_shared_credential_attribution: true,
      },
    }));
    expect(create.mutateAsync).toHaveBeenCalledWith(expect.objectContaining({
      body: expect.objectContaining({ based_on_release_id: 'APR-active' }),
    }));
  });

  it('retains the durable inactive receipt when post-create activation fails', async () => {
    query.data = active;
    create.mutateAsync.mockResolvedValueOnce({
      release: { id: 'APR-durable', version: 4 }, activated: false,
      validation: { canonical: true, digest: 'digest' },
    });
    activate.mutateAsync.mockRejectedValueOnce(new ApiError(503, 'activation_unavailable', {}));
    render(<TeamEscalationPolicyCard agent={agent} />);
    fireEvent.change(await screen.findByLabelText('Title'), { target: { value: 'Edited policy' } });
    fireEvent.click(screen.getByRole('button', { name: 'Save & activate' }));
    fireEvent.click(screen.getByRole('button', { name: 'Confirm' }));
    expect(await screen.findByText('Saved inactive v4 · APR-durable')).toBeInTheDocument();
    expect(screen.getByRole('status')).toHaveTextContent(/was saved inactive, but activation failed/i);
    expect(screen.getByRole('status')).toHaveTextContent(/Retry activation from this saved version/i);
    expect(screen.getByRole('status')).not.toHaveTextContent(/could not be saved/i);
  });
});
