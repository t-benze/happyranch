import { screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { http, HttpResponse } from 'msw';
import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest';
import { server } from '@/test/server';

const SLUG = 'hk-macau-tourism';

beforeEach(() => {
  // KbPage captures the build flag at module load. Give each case a fresh
  // route/page/provider graph so the enabled and disabled states cannot leak.
  vi.resetModules();
});

afterEach(() => {
  vi.unstubAllEnvs();
});

async function renderAppWithComposeEnabled(enabled: boolean): Promise<void> {
  vi.stubEnv('VITE_ENABLE_KB_COMPOSE', enabled ? 'true' : 'false');
  const [{ AppRoutes }, { renderWithProviders }] = await Promise.all([
    import('@/routes'),
    import('@/test/render'),
  ]);
  renderWithProviders(<AppRoutes />, { route: `/orgs/${SLUG}/kb` });
}

describe('KB compose write path', () => {
  test('submits POST /kb and navigates to detail', async () => {
    sessionStorage.setItem('happyranch.token', 'tok');
    let postedBody: Record<string, unknown> | null = null;
    let detailRequested = false;
    server.use(
      http.get('/api/v1/orgs', () =>
        HttpResponse.json({ orgs: [{ slug: SLUG, root: '/x' }] }),
      ),
      http.get(`/api/v1/orgs/${SLUG}/kb`, () =>
        HttpResponse.json({ entries: [] }),
      ),
      http.get(`/api/v1/orgs/${SLUG}/kb/stats`, () =>
        HttpResponse.json({ entries: [] }),
      ),
      http.get(`/api/v1/orgs/${SLUG}/dreams`, () =>
        HttpResponse.json({ dreams: [] }),
      ),
      http.post(`/api/v1/orgs/${SLUG}/kb`, async ({ request }) => {
        postedBody = (await request.json()) as Record<string, unknown>;
        return HttpResponse.json({
          slug: postedBody.slug,
          updated_at: '2026-05-19T12:00:00Z',
        });
      }),
      http.get(`/api/v1/orgs/${SLUG}/kb/policy/new-rule`, () => {
        detailRequested = true;
        return HttpResponse.json({
          slug: 'policy/new-rule',
          title: 'A new rule',
          type: 'precedent',
          topic: 'policy',
          tags: ['policy'],
          body: 'Body here',
          updated_at: '2026-05-19T12:00:00Z',
          authored_by: 'founder',
          source_task: null,
          related_entries: [],
        });
      }),
    );
    const user = userEvent.setup();
    await renderAppWithComposeEnabled(true);

    await user.click(await screen.findByRole('button', { name: /Compose…/ }));
    await user.type(screen.getByLabelText(/^Slug$/i), 'policy/new-rule');
    await user.type(screen.getByLabelText(/^Title$/i), 'A new rule');
    await user.type(screen.getByLabelText(/^Type$/i), 'precedent');
    await user.type(screen.getByLabelText(/^Topic$/i), 'policy');
    await user.type(screen.getByLabelText(/^Tags/i), 'policy');
    await user.type(screen.getByLabelText(/^Body/i), 'Body here');
    await user.click(screen.getByRole('button', { name: /Add entry/ }));

    await waitFor(() => {
      expect(postedBody).toMatchObject({
        slug: 'policy/new-rule',
        title: 'A new rule',
        type: 'precedent',
        topic: 'policy',
        tags: ['policy'],
        body: 'Body here',
        agent: 'founder',
      });
    });

    expect(await screen.findByRole('heading', { name: 'A new rule' })).toBeInTheDocument();
    expect(await screen.findByText('Body here')).toBeInTheDocument();
    expect(detailRequested).toBe(true);
  });

  test('Compose button is absent when flag is off', async () => {
    sessionStorage.setItem('happyranch.token', 'tok');
    server.use(
      http.get('/api/v1/orgs', () =>
        HttpResponse.json({ orgs: [{ slug: SLUG, root: '/x' }] }),
      ),
      http.get(`/api/v1/orgs/${SLUG}/kb`, () =>
        HttpResponse.json({ entries: [] }),
      ),
      http.get(`/api/v1/orgs/${SLUG}/kb/stats`, () =>
        HttpResponse.json({ entries: [] }),
      ),
      http.get(`/api/v1/orgs/${SLUG}/dreams`, () =>
        HttpResponse.json({ dreams: [] }),
      ),
    );
    await renderAppWithComposeEnabled(false);
    await screen.findByRole('heading', { name: /What the org has learned/ });
    expect(screen.queryByRole('button', { name: /Compose…/ })).toBeNull();
  });
});
