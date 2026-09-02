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
const query = { data: empty as typeof empty | undefined, isLoading: false, isError: false, error: null };
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
});
