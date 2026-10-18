import { screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { http, HttpResponse } from 'msw';
import { afterEach, describe, expect, test, vi } from 'vitest';
import { server } from '@/test/server';

/**
 * KB compose write path under the THR-305 build-time flag `kb_compose_ui`.
 *
 * Two kinds of cases:
 * - mocked: each case registers its own `@/config/features` value, then
 *   imports a fresh routes/provider graph, so on and off never share modules;
 * - real: the startup-resolved flag (development defaults, plus an explicit
 *   `VITE_ENABLE_KB_COMPOSE=true|false` as with `npm run test:write-path`).
 *   The real value is captured at file collection, BEFORE the conditional
 *   tests are registered; only the branch matching the startup runs.
 *
 * No KbPage/routes/provider module is imported statically by this file.
 */

const SLUG = 'hk-macau-tourism';
const FEATURES = '@/config/features';

// Collection time: drop any mock and module cache, then read the real flag.
vi.doUnmock(FEATURES);
vi.resetModules();
const REAL_KB_COMPOSE: boolean = (await import('@/config/features')).features.kb_compose_ui;

afterEach(() => {
  vi.doUnmock(FEATURES);
  vi.resetModules();
});

async function renderKb({ mocked }: { mocked?: boolean } = {}): Promise<boolean> {
  vi.doUnmock(FEATURES);
  vi.resetModules();
  if (mocked !== undefined) {
    vi.doMock(FEATURES, () => ({ features: Object.freeze({ work_hours_ui: true, kb_compose_ui: mocked }) }));
  }
  // Sequential on purpose: each dynamic import first settles the queued
  // doUnmock/doMock registrations, so the whole graph sees one flag module.
  const { features } = await import('@/config/features');
  const { AppRoutes } = await import('@/routes');
  const { renderWithProviders } = await import('@/test/render');
  renderWithProviders(<AppRoutes />, { route: `/orgs/${SLUG}/kb` });
  return features.kb_compose_ui;
}

function serveKb(): { posts: Array<Record<string, unknown>>; detail: { requested: boolean } } {
  sessionStorage.setItem('happyranch.token', 'tok');
  const posts: Array<Record<string, unknown>> = [];
  const detail = { requested: false };
  server.use(
    http.get('/api/v1/orgs', () => HttpResponse.json({ orgs: [{ slug: SLUG, root: '/x' }] })),
    http.get(`/api/v1/orgs/${SLUG}/kb`, () => HttpResponse.json({ entries: [] })),
    http.get(`/api/v1/orgs/${SLUG}/kb/stats`, () => HttpResponse.json({ entries: [] })),
    http.get(`/api/v1/orgs/${SLUG}/dreams`, () => HttpResponse.json({ dreams: [] })),
    http.post(`/api/v1/orgs/${SLUG}/kb`, async ({ request }) => {
      const body = (await request.json()) as Record<string, unknown>;
      posts.push(body);
      return HttpResponse.json({ slug: body.slug, updated_at: '2026-05-19T12:00:00Z' });
    }),
    http.get(`/api/v1/orgs/${SLUG}/kb/policy/new-rule`, () => {
      detail.requested = true;
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
  return { posts, detail };
}

/** Compose → POST → detail. Five assertions. */
async function expectComposeWrites(ledger: ReturnType<typeof serveKb>): Promise<void> {
  const user = userEvent.setup();
  const compose = await screen.findByRole('button', { name: /Compose…/ });
  expect(compose).toBeInTheDocument();
  await user.click(compose);
  await user.type(screen.getByLabelText(/^Slug$/i), 'policy/new-rule');
  await user.type(screen.getByLabelText(/^Title$/i), 'A new rule');
  await user.type(screen.getByLabelText(/^Type$/i), 'precedent');
  await user.type(screen.getByLabelText(/^Topic$/i), 'policy');
  await user.type(screen.getByLabelText(/^Tags/i), 'policy');
  await user.type(screen.getByLabelText(/^Body/i), 'Body here');
  await user.click(screen.getByRole('button', { name: /Add entry/ }));
  await waitFor(() => {
    if (ledger.posts.length === 0) throw new Error('no POST /kb yet');
  });
  expect(ledger.posts).toEqual([
    expect.objectContaining({
      slug: 'policy/new-rule',
      title: 'A new rule',
      type: 'precedent',
      topic: 'policy',
      tags: ['policy'],
      body: 'Body here',
      agent: 'founder',
    }),
  ]);
  expect(await screen.findByRole('heading', { name: 'A new rule' })).toBeInTheDocument();
  expect(await screen.findByText('Body here')).toBeInTheDocument();
  expect(ledger.detail.requested).toBe(true);
}

/** Settled KB page, no Compose, no POST. Three assertions. */
async function expectNoCompose(ledger: ReturnType<typeof serveKb>): Promise<void> {
  expect(await screen.findByRole('heading', { name: /What the org has learned/ })).toBeInTheDocument();
  expect(screen.queryByRole('button', { name: /Compose…/ })).toBeNull();
  expect(ledger.posts).toHaveLength(0);
}

describe('KB compose write path — mocked flag', () => {
  test('flag on: submits POST /kb and navigates to detail', async () => {
    expect.assertions(6);
    const ledger = serveKb();
    expect(await renderKb({ mocked: true })).toBe(true);
    await expectComposeWrites(ledger);
  });

  test('flag off: Compose button is absent and nothing is posted', async () => {
    expect.assertions(4);
    const ledger = serveKb();
    expect(await renderKb({ mocked: false })).toBe(false);
    await expectNoCompose(ledger);
  });
});

describe(`KB compose write path — real startup flag (kb_compose_ui=${REAL_KB_COMPOSE})`, () => {
  test.runIf(REAL_KB_COMPOSE)('real on: submits POST /kb and navigates to detail', async () => {
    expect.assertions(6);
    const ledger = serveKb();
    expect(await renderKb()).toBe(REAL_KB_COMPOSE);
    await expectComposeWrites(ledger);
  });

  test.skipIf(REAL_KB_COMPOSE)('real off: Compose button is absent and nothing is posted', async () => {
    expect.assertions(4);
    const ledger = serveKb();
    expect(await renderKb()).toBe(REAL_KB_COMPOSE);
    await expectNoCompose(ledger);
  });
});
