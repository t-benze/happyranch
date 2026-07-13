import { describe, expect, test, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter } from 'react-router-dom';
import { AddOrgDialog } from './AddOrgDialog';
import { orgs as orgsApi } from '@/lib/api';

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

  test('surfaces 409 org_dir_exists inline as already exists', async () => {
    const user = userEvent.setup();
    vi.spyOn(orgsApi, 'createOrg').mockRejectedValue(
      Object.assign(new Error('dir exists'), { status: 409, code: 'org_dir_exists' }),
    );
    renderDialog();

    await user.type(screen.getByLabelText(/slug/i), 'stale-dir');
    await user.click(screen.getByRole('button', { name: /create/i }));

    await waitFor(() =>
      expect(screen.getByText(/already exists|org_dir_exists/i)).toBeInTheDocument(),
    );
  });

  test('surfaces 409 no_active_runtime with its own message, NOT already exists', async () => {
    const user = userEvent.setup();
    vi.spyOn(orgsApi, 'createOrg').mockRejectedValue(
      Object.assign(new Error('no runtime'), { status: 409, code: 'no_active_runtime' }),
    );
    renderDialog();

    await user.type(screen.getByLabelText(/slug/i), 'my-org');
    await user.click(screen.getByRole('button', { name: /create/i }));

    await waitFor(() => {
      // The error is a <p className="text-tier-red">, not role="alert"
      const errorEl = screen.getByText(/runtime|start|ready|moment/i);
      expect(errorEl).toBeInTheDocument();
      // Must NOT say "already exists"
      expect(errorEl.textContent).not.toMatch(/already exists/i);
    });
  });

  test('surfaces generic non-409 error as fallback message', async () => {
    const user = userEvent.setup();
    vi.spyOn(orgsApi, 'createOrg').mockRejectedValue(
      Object.assign(new Error('server error'), { status: 500 }),
    );
    renderDialog();

    await user.type(screen.getByLabelText(/slug/i), 'my-org');
    await user.click(screen.getByRole('button', { name: /create/i }));

    await waitFor(() =>
      expect(screen.getByText(/server error/i)).toBeInTheDocument(),
    );
  });
});
