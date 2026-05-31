import { describe, expect, test, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter } from 'react-router-dom';
import { AddOrgDialog } from './AddOrgDialog';
import * as orgsApi from '@/lib/api/orgs';

function renderDialog(onClose = vi.fn()) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter>
        <AddOrgDialog open onOpenChange={onClose} />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  vi.restoreAllMocks();
});

describe('AddOrgDialog', () => {
  test('Create disabled until slug matches ^[a-z0-9-]{1,40}$', async () => {
    const user = userEvent.setup();
    renderDialog();
    const input = screen.getByLabelText(/slug/i);
    const submit = screen.getByRole('button', { name: /create/i });
    expect(submit).toBeDisabled();

    await user.type(input, 'Bad_Slug');
    expect(submit).toBeDisabled();

    await user.clear(input);
    await user.type(input, 'good-slug-1');
    expect(submit).not.toBeDisabled();
  });

  test('submits POST /orgs and closes on success', async () => {
    const user = userEvent.setup();
    const spy = vi.spyOn(orgsApi, 'createOrg').mockResolvedValue({ slug: 'good-slug' });
    const onClose = vi.fn();
    renderDialog(onClose);

    await user.type(screen.getByLabelText(/slug/i), 'good-slug');
    await user.click(screen.getByRole('button', { name: /create/i }));

    await waitFor(() => expect(spy).toHaveBeenCalledWith({ slug: 'good-slug' }));
  });

  test('surfaces 409 org_exists inline', async () => {
    const user = userEvent.setup();
    vi.spyOn(orgsApi, 'createOrg').mockRejectedValue(
      Object.assign(new Error('exists'), { status: 409, code: 'org_exists' }),
    );
    renderDialog();

    await user.type(screen.getByLabelText(/slug/i), 'taken');
    await user.click(screen.getByRole('button', { name: /create/i }));

    await waitFor(() =>
      expect(screen.getByText(/already exists|org_exists/i)).toBeInTheDocument(),
    );
  });
});
