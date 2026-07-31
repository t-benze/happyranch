import { screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { http, HttpResponse } from 'msw';
import { describe, expect, test } from 'vitest';
import { renderWithProviders } from '@/test/render';
import { server } from '@/test/server';
import { AdapterManagementSection } from './AdapterManagementSection';

interface AdapterEntry {
  id: string;
  name: string;
  executable: string;
  executable_hash: string;
  version: string;
  capabilities: string[];
  contract_version: number;
  workspace_adapter: string;
  status: string;
  registered_at: string;
  registered_by: string;
  approved_at: string | null;
  approved_by: string | null;
  intended_profile_name: string | null;
  eligibility: string | null;
}

const APPROVED_UNBOUND: AdapterEntry = {
  id: 'test-adapter',
  name: 'test-adapter',
  executable: '/usr/bin/echo',
  executable_hash: 'abc123',
  version: '1.0.0',
  capabilities: ['test'],
  contract_version: 1,
  workspace_adapter: 'pi',
  status: 'approved',
  registered_at: '2026-07-31T00:00:00Z',
  registered_by: 'test',
  approved_at: '2026-07-31T01:00:00Z',
  approved_by: 'founder',
  intended_profile_name: 'test-profile',
  eligibility: 'ready_to_bind',
};

const APPROVED_BOUND: AdapterEntry = {
  id: 'bound-adapter',
  name: 'bound-adapter',
  executable: '/usr/bin/true',
  executable_hash: 'def456',
  version: '1.0.0',
  capabilities: [],
  contract_version: 1,
  workspace_adapter: 'pi',
  status: 'approved',
  registered_at: '2026-07-31T00:00:00Z',
  registered_by: 'test',
  approved_at: '2026-07-31T01:00:00Z',
  approved_by: 'founder',
  intended_profile_name: 'bound-profile',
  eligibility: 'already_bound',
};

const PENDING_ADAPTER: AdapterEntry = {
  id: 'pending-adapter',
  name: 'pending-adapter',
  executable: '/usr/bin/false',
  executable_hash: 'ghi789',
  version: '1.0.0',
  capabilities: [],
  contract_version: 1,
  workspace_adapter: 'pi',
  status: 'pending',
  registered_at: '2026-07-31T00:00:00Z',
  registered_by: 'test',
  approved_at: null,
  approved_by: null,
  intended_profile_name: null,
  eligibility: null,
};

function stubAdapters(adapters: AdapterEntry[]) {
  server.use(
    http.get('/api/v1/runtime/adapters', () => HttpResponse.json(adapters)),
  );
}

function render() {
  sessionStorage.setItem('happyranch.token', 'tok');
  renderWithProviders(<AdapterManagementSection />);
}

describe('AdapterManagementSection (Settings → Executors → Custom Adapters)', () => {
  test('empty: renders the empty state when no adapters registered', async () => {
    stubAdapters([]);
    render();

    expect(await screen.findByTestId('adapter-list-empty')).toBeInTheDocument();
    expect(screen.queryByTestId('adapter-rows')).not.toBeInTheDocument();
  });

  test('populated: renders one row per adapter with id, status, name, hash', async () => {
    stubAdapters([APPROVED_UNBOUND, APPROVED_BOUND]);
    render();

    const rowA = await screen.findByTestId('adapter-row-test-adapter');
    expect(within(rowA).getAllByText('test-adapter').length).toBeGreaterThan(0);
    expect(within(rowA).getByTestId('adapter-status-test-adapter')).toHaveTextContent(
      'approved',
    );
    expect(within(rowA).getByText('abc123')).toBeInTheDocument();

    const rowB = screen.getByTestId('adapter-row-bound-adapter');
    expect(within(rowB).getAllByText('bound-adapter').length).toBeGreaterThan(0);
    expect(within(rowB).getByTestId('adapter-status-bound-adapter')).toHaveTextContent(
      'approved',
    );
  });

  test('remove: eligible adapter shows guarded confirm/cancel', async () => {
    const user = userEvent.setup();
    stubAdapters([APPROVED_UNBOUND]);
    render();

    const row = await screen.findByTestId('adapter-row-test-adapter');

    // The remove button is visible.
    const removeBtn = within(row).getByTestId('adapter-remove-test-adapter');
    expect(removeBtn).toBeInTheDocument();

    // First click arms confirm/cancel.
    await user.click(removeBtn);
    expect(
      within(row).getByTestId('adapter-confirm-remove-test-adapter'),
    ).toBeInTheDocument();
    expect(within(row).getByRole('button', { name: /cancel/i })).toBeInTheDocument();
  });

  test('remove: cancel collapses back to initial state', async () => {
    const user = userEvent.setup();
    stubAdapters([APPROVED_UNBOUND]);
    render();

    const row = await screen.findByTestId('adapter-row-test-adapter');
    await user.click(within(row).getByTestId('adapter-remove-test-adapter'));

    // Cancel should collapse without removing.
    await user.click(within(row).getByRole('button', { name: /cancel/i }));
    expect(
      within(row).queryByTestId('adapter-confirm-remove-test-adapter'),
    ).not.toBeInTheDocument();
    expect(within(row).getByTestId('adapter-remove-test-adapter')).toBeInTheDocument();
  });

  test('remove: confirm sends exact DELETE with body/snapshot, refetches list', async () => {
    const user = userEvent.setup();
    const deleted: string[] = [];
    const receivedBodies: unknown[] = [];

    let store: AdapterEntry[] = [APPROVED_UNBOUND, APPROVED_BOUND];
    server.use(
      http.get('/api/v1/runtime/adapters', () => HttpResponse.json(store)),
      http.delete('/api/v1/runtime/adapters/:id', async ({ params, request }) => {
        const id = String(params.id);
        const body = await request.json();
        deleted.push(id);
        receivedBodies.push(body);
        store = store.filter((a) => a.id !== id);
        return HttpResponse.json({ id, removed: true, name: 'test-adapter' });
      }),
    );
    render();

    const row = await screen.findByTestId('adapter-row-test-adapter');
    await user.click(within(row).getByTestId('adapter-remove-test-adapter'));
    await user.click(within(row).getByTestId('adapter-confirm-remove-test-adapter'));

    // The row for the removed adapter disappears.
    await screen.findByTestId('adapter-row-bound-adapter');
    expect(screen.queryByTestId('adapter-row-test-adapter')).not.toBeInTheDocument();
    expect(screen.getByTestId('adapter-row-bound-adapter')).toBeInTheDocument();

    // Verify the DELETE body matches the snapshot.
    expect(deleted).toEqual(['test-adapter']);
    expect(receivedBodies).toHaveLength(1);
    expect((receivedBodies[0] as Record<string, unknown>).executable).toBe(
      APPROVED_UNBOUND.executable,
    );
    expect((receivedBodies[0] as Record<string, unknown>).executable_hash).toBe(
      APPROVED_UNBOUND.executable_hash,
    );
    expect((receivedBodies[0] as Record<string, unknown>).name).toBe(
      APPROVED_UNBOUND.name,
    );
  });

  test('remove: 404 (already gone) refetches list gracefully, no error banner', async () => {
    const user = userEvent.setup();
    let store: AdapterEntry[] = [APPROVED_UNBOUND];
    server.use(
      http.get('/api/v1/runtime/adapters', () => HttpResponse.json(store)),
      http.delete('/api/v1/runtime/adapters/:id', () => {
        store = [];
        return HttpResponse.json(
          { detail: 'Adapter not found.' },
          { status: 404 },
        );
      }),
    );
    render();

    const row = await screen.findByTestId('adapter-row-test-adapter');
    await user.click(within(row).getByTestId('adapter-remove-test-adapter'));
    await user.click(within(row).getByTestId('adapter-confirm-remove-test-adapter'));

    // Graceful: the list refetches to empty, no error surfaced.
    expect(await screen.findByTestId('adapter-list-empty')).toBeInTheDocument();
    expect(
      screen.queryByTestId('adapter-remove-error-test-adapter'),
    ).not.toBeInTheDocument();
  });

  test('remove: failure state preserves row and shows error with refetch capability', async () => {
    const user = userEvent.setup();
    stubAdapters([APPROVED_UNBOUND]);
    server.use(
      http.delete('/api/v1/runtime/adapters/:id', () =>
        HttpResponse.json(
          { detail: 'Cannot remove: profile is bound.' },
          { status: 422 },
        ),
      ),
    );
    render();

    const row = await screen.findByTestId('adapter-row-test-adapter');
    await user.click(within(row).getByTestId('adapter-remove-test-adapter'));
    await user.click(within(row).getByTestId('adapter-confirm-remove-test-adapter'));

    // Error is surfaced and the row is still present (refetchable).
    expect(
      await within(row).findByTestId('adapter-remove-error-test-adapter'),
    ).toBeInTheDocument();
    expect(screen.getByTestId('adapter-row-test-adapter')).toBeInTheDocument();
    expect(
      within(row).getByTestId('adapter-remove-error-test-adapter'),
    ).toHaveTextContent(/profile is bound/i);
  });

  test('bound adapter: no remove affordance when eligibility is already_bound', async () => {
    stubAdapters([APPROVED_BOUND]);
    render();

    const row = await screen.findByTestId('adapter-row-bound-adapter');
    expect(
      within(row).queryByTestId('adapter-remove-bound-adapter'),
    ).not.toBeInTheDocument();
  });

  test('pending adapter: no remove affordance when eligibility is null', async () => {
    stubAdapters([PENDING_ADAPTER]);
    render();

    const row = await screen.findByTestId('adapter-row-pending-adapter');
    expect(
      within(row).queryByTestId('adapter-remove-pending-adapter'),
    ).not.toBeInTheDocument();
  });

  test('error: failed list load surfaces an alert, not an opaque blank', async () => {
    server.use(
      http.get('/api/v1/runtime/adapters', () =>
        HttpResponse.json({ detail: 'boom' }, { status: 500 }),
      ),
    );
    render();

    expect(
      await screen.findByText(/could not load custom adapters/i),
    ).toBeInTheDocument();
  });

  test('differently-named custom profile referencing this adapter: eligibility already_bound, no misleading action', async () => {
    // Simulate a custom profile named "other-profile" that references this
    // adapter — the server marks eligibility as already_bound.
    const adapterWithDifferentProfile: AdapterEntry = {
      ...APPROVED_UNBOUND,
      id: 'diff-profile-adapter',
      name: 'diff-profile-adapter',
      intended_profile_name: 'other-profile',
      eligibility: 'already_bound',
    };
    stubAdapters([adapterWithDifferentProfile]);
    render();

    const row = await screen.findByTestId('adapter-row-diff-profile-adapter');
    // No remove affordance — the UI never offers a misleading action.
    expect(
      within(row).queryByTestId('adapter-remove-diff-profile-adapter'),
    ).not.toBeInTheDocument();
  });
});
