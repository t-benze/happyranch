import { screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { http, HttpResponse } from 'msw';
import { describe, expect, test } from 'vitest';
import { AppRoutes } from '@/routes';
import { renderWithProviders } from '@/test/render';
import { server } from '@/test/server';

const SLUG = 'hk-macau-tourism';

const ENTRY_A = {
  slug: 'policy/refund-thresholds',
  title: 'Refund authority by tier',
  type: 'precedent',
  topic: 'finance',
  tags: ['policy', 'finance', 'customer-care'],
  body: '# Refund authority\n\nThe CX Manager may approve refunds up to $150.',
  updated_at: '2026-05-16T09:00:00Z',
  authored_by: 'founder',
  source_task: 'TASK-0042',
  related_entries: ['intake/spanish-walk-ins'],
};

const ENTRY_B = {
  slug: 'intake/spanish-walk-ins',
  title: 'Spanish-speaking walk-in flow',
  type: 'sop',
  topic: 'intake',
  tags: ['intake'],
  body: '# Spanish walk-ins',
  updated_at: '2026-05-12T11:00:00Z',
  authored_by: 'intake_manager',
  source_task: null,
  related_entries: [],
};

function stubBase() {
  server.use(
    http.get('/api/v1/orgs', () =>
      HttpResponse.json({ orgs: [{ slug: SLUG, root: '/x' }] }),
    ),
    http.get(`/api/v1/orgs/${SLUG}/kb`, () =>
      HttpResponse.json({ entries: [ENTRY_A, ENTRY_B] }),
    ),
  );
}

describe('KbPage — read path', () => {
  test('renders entries from /kb', async () => {
    sessionStorage.setItem('grassland.token', 'tok');
    stubBase();
    renderWithProviders(<AppRoutes />, { route: `/orgs/${SLUG}/kb` });
    await waitFor(() => {
      expect(screen.getByText(/Refund authority by tier/)).toBeInTheDocument();
      expect(screen.getByText(/Spanish-speaking walk-in flow/)).toBeInTheDocument();
    });
  });

  test('filters by type via server param', async () => {
    sessionStorage.setItem('grassland.token', 'tok');
    let serverParams: string | null = null;
    server.use(
      http.get('/api/v1/orgs', () =>
        HttpResponse.json({ orgs: [{ slug: SLUG, root: '/x' }] }),
      ),
      http.get(`/api/v1/orgs/${SLUG}/kb`, ({ request }) => {
        const url = new URL(request.url);
        serverParams = url.searchParams.get('type');
        const all = [ENTRY_A, ENTRY_B];
        const filtered = serverParams
          ? all.filter((e) => e.type === serverParams)
          : all;
        return HttpResponse.json({ entries: filtered });
      }),
    );
    const user = userEvent.setup();
    renderWithProviders(<AppRoutes />, { route: `/orgs/${SLUG}/kb` });
    await screen.findByText(/Refund authority by tier/);
    await user.click(screen.getByRole('button', { name: /^precedent$/ }));
    await waitFor(() => expect(serverParams).toBe('precedent'));
    await waitFor(() =>
      expect(screen.queryByText(/Spanish-speaking walk-in flow/)).not.toBeInTheDocument(),
    );
  });

  test('client-side tag filter narrows the list', async () => {
    sessionStorage.setItem('grassland.token', 'tok');
    stubBase();
    const user = userEvent.setup();
    renderWithProviders(<AppRoutes />, { route: `/orgs/${SLUG}/kb` });
    await screen.findByText(/Refund authority by tier/);
    await user.click(screen.getByRole('button', { name: /^intake$/ }));
    await waitFor(() =>
      expect(screen.queryByText(/Refund authority by tier/)).not.toBeInTheDocument(),
    );
    expect(screen.getByText(/Spanish-speaking walk-in flow/)).toBeInTheDocument();
  });

  test('opens drawer with markdown + source-task badge linking to /tasks', async () => {
    sessionStorage.setItem('grassland.token', 'tok');
    stubBase();
    server.use(
      http.get(`/api/v1/orgs/${SLUG}/kb/policy/refund-thresholds`, () =>
        HttpResponse.json(ENTRY_A),
      ),
    );
    const user = userEvent.setup();
    renderWithProviders(<AppRoutes />, { route: `/orgs/${SLUG}/kb` });
    await user.click(await screen.findByText(/Refund authority by tier/));
    await waitFor(() =>
      expect(screen.getByText(/CX Manager may approve refunds/)).toBeInTheDocument(),
    );
    const badge = screen.getByText('TASK-0042');
    expect(badge.closest('a')).toHaveAttribute(
      'href',
      `/orgs/${SLUG}/tasks/TASK-0042`,
    );
  });

  test('search box switches active query to /kb/search', async () => {
    sessionStorage.setItem('grassland.token', 'tok');
    let searchHit = false;
    server.use(
      http.get('/api/v1/orgs', () =>
        HttpResponse.json({ orgs: [{ slug: SLUG, root: '/x' }] }),
      ),
      http.get(`/api/v1/orgs/${SLUG}/kb`, () =>
        HttpResponse.json({ entries: [ENTRY_A, ENTRY_B] }),
      ),
      http.get(`/api/v1/orgs/${SLUG}/kb/search`, () => {
        searchHit = true;
        return HttpResponse.json({ entries: [ENTRY_A] });
      }),
    );
    const user = userEvent.setup();
    renderWithProviders(<AppRoutes />, { route: `/orgs/${SLUG}/kb` });
    await screen.findByText(/Refund authority by tier/);
    await user.type(screen.getByPlaceholderText(/Search entries/i), 'refund');
    await waitFor(() => expect(searchHit).toBe(true), { timeout: 2000 });
  });

  test('type filter persists when search becomes active', async () => {
    sessionStorage.setItem('grassland.token', 'tok');
    server.use(
      http.get('/api/v1/orgs', () =>
        HttpResponse.json({ orgs: [{ slug: SLUG, root: '/x' }] }),
      ),
      http.get(`/api/v1/orgs/${SLUG}/kb`, ({ request }) => {
        const url = new URL(request.url);
        const t = url.searchParams.get('type');
        const all = [ENTRY_A, ENTRY_B];
        return HttpResponse.json({
          entries: t ? all.filter((e) => e.type === t) : all,
        });
      }),
      // Search endpoint deliberately returns BOTH types — the page must
      // re-apply the active type filter client-side, per spec §4.
      http.get(`/api/v1/orgs/${SLUG}/kb/search`, () =>
        HttpResponse.json({ entries: [ENTRY_A, ENTRY_B] }),
      ),
    );
    const user = userEvent.setup();
    renderWithProviders(<AppRoutes />, { route: `/orgs/${SLUG}/kb` });
    await screen.findByText(/Refund authority by tier/);
    await user.click(screen.getByRole('button', { name: /^precedent$/ }));
    await waitFor(() =>
      expect(screen.queryByText(/Spanish-speaking walk-in flow/)).not.toBeInTheDocument(),
    );
    await user.type(screen.getByPlaceholderText(/Search entries/i), 'a');
    // Wait for debounced search to land and the result list to settle.
    await waitFor(() =>
      expect(screen.getByText(/Refund authority by tier/)).toBeInTheDocument(),
      { timeout: 2000 },
    );
    expect(screen.queryByText(/Spanish-speaking walk-in flow/)).not.toBeInTheDocument();
  });
});
