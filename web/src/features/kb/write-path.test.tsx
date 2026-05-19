import { screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { http, HttpResponse } from 'msw';
import { afterAll, beforeAll, describe, expect, test } from 'vitest';
import { AppRoutes } from '@/routes';
import { renderWithProviders } from '@/test/render';
import { server } from '@/test/server';

const SLUG = 'hk-macau-tourism';
const FLAG_ON = import.meta.env.VITE_ENABLE_KB_COMPOSE === 'true';

beforeAll(() => {
  // The flag is read at module-load time; tests are run with it on via
  // VITE_ENABLE_KB_COMPOSE=true (set in package.json's test:write-path
  // script, or by the CI matrix). When unset, the suite is skipped.
});
afterAll(() => {
  // no-op
});

(FLAG_ON ? describe : describe.skip)('KB compose write path', () => {
  test('submits POST /kb and navigates to detail', async () => {
    sessionStorage.setItem('grassland.token', 'tok');
    let postedBody: Record<string, unknown> | null = null;
    server.use(
      http.get('/api/v1/orgs', () =>
        HttpResponse.json({ orgs: [{ slug: SLUG, root: '/x' }] }),
      ),
      http.get(`/api/v1/orgs/${SLUG}/kb`, () =>
        HttpResponse.json({ entries: [] }),
      ),
      http.post(`/api/v1/orgs/${SLUG}/kb`, async ({ request }) => {
        postedBody = (await request.json()) as Record<string, unknown>;
        return HttpResponse.json({
          slug: postedBody.slug,
          updated_at: '2026-05-19T12:00:00Z',
        });
      }),
      http.get(`/api/v1/orgs/${SLUG}/kb/policy/new-rule`, () =>
        HttpResponse.json({
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
        }),
      ),
    );
    const user = userEvent.setup();
    renderWithProviders(<AppRoutes />, { route: `/orgs/${SLUG}/kb` });

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
  });
});

describe('KB compose write path (flag off)', () => {
  test('Compose button is absent when flag is off', async () => {
    if (FLAG_ON) return;
    sessionStorage.setItem('grassland.token', 'tok');
    server.use(
      http.get('/api/v1/orgs', () =>
        HttpResponse.json({ orgs: [{ slug: SLUG, root: '/x' }] }),
      ),
      http.get(`/api/v1/orgs/${SLUG}/kb`, () =>
        HttpResponse.json({ entries: [] }),
      ),
    );
    renderWithProviders(<AppRoutes />, { route: `/orgs/${SLUG}/kb` });
    await screen.findByRole('heading', { name: /Knowledge base/ });
    expect(screen.queryByRole('button', { name: /Compose…/ })).toBeNull();
  });
});
