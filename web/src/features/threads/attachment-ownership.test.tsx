/**
 * TASK-8616 — captured-destination and late-result ownership cases (accepted
 * case-design Group 8: C8.1, C8.2, C8.4, C8.5).
 *
 * These run through the real `<AppProvider>` + router + MSW fetch boundary
 * (`renderWithProviders`). MSW is browser-lifecycle evidence only.
 */
import { fireEvent, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { http, HttpResponse } from 'msw';
import { useNavigate } from 'react-router-dom';
import { beforeEach, describe, expect, test } from 'vitest';
import { AppRoutes } from '@/routes';
import { renderWithProviders } from '@/test/render';
import { server } from '@/test/server';

function orgHandlers(slug: string, threadIds: string[]) {
  const handlers = [
    http.get('/api/v1/orgs', () =>
      HttpResponse.json({
        orgs: [
          { slug: 'alpha', root: '/a' },
          { slug: 'beta', root: '/b' },
        ],
      }),
    ),
    http.get(`/api/v1/orgs/${slug}/agents`, () => HttpResponse.json({ agents: [] })),
    http.get(`/api/v1/orgs/${slug}/threads`, () => HttpResponse.json({ threads: [] })),
    http.get(`/api/v1/orgs/${slug}/threads/events`, () =>
      HttpResponse.text('', { headers: { 'content-type': 'text/event-stream' } }),
    ),
    http.get(`/api/v1/orgs/${slug}/tokens`, () => HttpResponse.json({ rollup: [] })),
  ];
  for (const threadId of threadIds) {
    handlers.push(
      http.get(`/api/v1/orgs/${slug}/threads/${threadId}`, () =>
        HttpResponse.json({
          thread_id: threadId,
          subject: `Subject ${slug}/${threadId}`,
          status: 'open',
          started_at: 'now',
          archived_at: null,
          forwarded_from_id: null,
          forwarded_from_kind: null,
          turn_cap: 500,
          turns_used: 0,
          summary: null,
          transcript_path: null,
          participants: ['agent_a'],
          messages: [],
        }),
      ),
      http.get(`/api/v1/orgs/${slug}/threads/${threadId}/messages`, () =>
        HttpResponse.json({ messages: [] }),
      ),
      http.get(`/api/v1/orgs/${slug}/threads/${threadId}/tail`, () =>
        HttpResponse.text('', { headers: { 'content-type': 'text/event-stream' } }),
      ),
    );
  }
  return handlers;
}

function requestedName(request: Request): string {
  return new URL(request.url).searchParams.get('name') ?? '';
}

function NavTo({ to, label }: { to: string; label: string }) {
  const navigate = useNavigate();
  return (
    <button type="button" onClick={() => navigate(to)}>
      {label}
    </button>
  );
}

beforeEach(() => {
  localStorage.clear();
});

describe('captured destination and late-result ownership (C8)', () => {
  test('an org switch keeping the same thread id does not block or clear the new view (C8.2)', async () => {
    sessionStorage.setItem('happyranch.token', 'tok');
    server.use(...orgHandlers('alpha', ['THR-001']), ...orgHandlers('beta', ['THR-001']));
    const sends: { url: string; body: unknown }[] = [];
    let releaseUpload: (value: unknown) => void = () => {};
    const held = new Promise((resolve) => { releaseUpload = resolve; });
    server.use(
      http.post('/api/v1/orgs/alpha/artifacts', async ({ request }) => {
        await held;
        return HttpResponse.json({ name: requestedName(request), size_bytes: 3, modified_at: 'now' });
      }),
      http.post('/api/v1/orgs/beta/artifacts', async ({ request }) =>
        HttpResponse.json({ name: requestedName(request), size_bytes: 3, modified_at: 'now' }),
      ),
      http.post('/api/v1/orgs/alpha/threads/THR-001/send', async ({ request }) => {
        sends.push({
          url: new URL(request.url).pathname + new URL(request.url).search,
          body: await request.json(),
        });
        return HttpResponse.json({ thread_id: 'x', seq: 2 });
      }),
      http.post('/api/v1/orgs/beta/threads/THR-001/send', async ({ request }) => {
        sends.push({ url: new URL(request.url).pathname, body: await request.json() });
        return HttpResponse.json({ thread_id: 'x', seq: 2 });
      }),
    );

    const user = userEvent.setup();
    renderWithProviders(
      <>
        <AppRoutes />
        <NavTo to="/orgs/beta/threads/THR-001" label="nav-beta" />
      </>,
      { route: '/orgs/alpha/threads/THR-001' },
    );
    const alphaComposer = await screen.findByLabelText(/Compose follow-up/i);
    await user.type(alphaComposer, 'A draft');
    // Settle the 300 ms draft debounce BEFORE navigating so the alpha-key write
    // is durable and cannot be cancelled by the next org's keystrokes.
    await waitFor(
      () => expect(localStorage.getItem('happyranch:draft:alpha:THR-001')).toBe('A draft'),
      { timeout: 1500 },
    );
    await user.upload(
      screen.getByLabelText(/Attach files/i),
      new File(['aaa'], 'a.txt', { type: 'text/plain' }),
    );
    fireEvent.click(screen.getByRole('button', { name: /^Send$/i }));

    // Switch org while A's upload is still held.
    await user.click(await screen.findByRole('button', { name: 'nav-beta' }));
    const betaComposer = await screen.findByLabelText(/Compose follow-up/i);
    // The replacement view must not inherit A's upload-pending latch.
    await waitFor(() => expect(betaComposer).not.toBeDisabled());
    await user.type(betaComposer, 'B draft');

    releaseUpload(undefined);
    await waitFor(() => expect(sends).toHaveLength(1));
    // A's request stayed at its captured destination.
    expect(sends[0].url).toBe('/api/v1/orgs/alpha/threads/THR-001/send');
    expect(sends.some((s) => s.url.includes('/orgs/beta/'))).toBe(false);

    // A's late success must not clear B's draft; settle the 300 ms debounce.
    await new Promise((resolve) => setTimeout(resolve, 350));
    expect((screen.getByLabelText(/Compose follow-up/i) as HTMLTextAreaElement).value).toBe(
      'B draft',
    );
    // B's own draft key is the beta key; A's draft persisted under the alpha key.
    expect(localStorage.getItem('happyranch:draft:alpha:THR-001')).toBe('A draft');
    expect(localStorage.getItem('happyranch:draft:beta:THR-001')).toBe('B draft');
  });

  test('A -> B -> A does not let the stale A success clear the new A draft (C8.1b)', async () => {
    sessionStorage.setItem('happyranch.token', 'tok');
    server.use(...orgHandlers('alpha', ['THR-001', 'THR-002']));
    const sends: { url: string; body: unknown }[] = [];
    let releaseUpload: (value: unknown) => void = () => {};
    const held = new Promise((resolve) => { releaseUpload = resolve; });
    server.use(
      http.post('/api/v1/orgs/alpha/artifacts', async ({ request }) => {
        await held;
        return HttpResponse.json({ name: requestedName(request), size_bytes: 3, modified_at: 'now' });
      }),
      http.post('/api/v1/orgs/alpha/threads/THR-001/send', async ({ request }) => {
        sends.push({ url: new URL(request.url).pathname, body: await request.json() });
        return HttpResponse.json({ thread_id: 'x', seq: 2 });
      }),
      http.post('/api/v1/orgs/alpha/threads/THR-002/send', async ({ request }) => {
        sends.push({ url: new URL(request.url).pathname, body: await request.json() });
        return HttpResponse.json({ thread_id: 'x', seq: 2 });
      }),
    );

    const user = userEvent.setup();
    renderWithProviders(
      <>
        <AppRoutes />
        <NavTo to="/orgs/alpha/threads/THR-002" label="nav-b" />
        <NavTo to="/orgs/alpha/threads/THR-001" label="nav-a" />
      </>,
      { route: '/orgs/alpha/threads/THR-001' },
    );
    const composer = await screen.findByLabelText(/Compose follow-up/i);
    await user.type(composer, 'first A');
    await user.upload(
      screen.getByLabelText(/Attach files/i),
      new File(['aaa'], 'a.txt', { type: 'text/plain' }),
    );
    fireEvent.click(screen.getByRole('button', { name: /^Send$/i }));

    await user.click(await screen.findByRole('button', { name: 'nav-b' }));
    await screen.findByLabelText(/Compose follow-up/i);
    await user.click(await screen.findByRole('button', { name: 'nav-a' }));
    const aComposer = await screen.findByLabelText(/Compose follow-up/i);
    await waitFor(() => expect(aComposer).not.toBeDisabled());
    await user.type(aComposer, 'second A');

    releaseUpload(undefined);
    await waitFor(() => expect(sends).toHaveLength(1));
    expect(sends[0].url).toBe('/api/v1/orgs/alpha/threads/THR-001/send');

    await new Promise((resolve) => setTimeout(resolve, 350));
    // The stale completion belongs to a departed generation; it must not clear
    // the fresh draft typed after returning to A.
    expect((screen.getByLabelText(/Compose follow-up/i) as HTMLTextAreaElement).value).toBe(
      'second A',
    );
  });

  test('full unmount discards pending chips and issues no duplicate send (C8.4)', async () => {
    sessionStorage.setItem('happyranch.token', 'tok');
    server.use(...orgHandlers('alpha', ['THR-001']));
    const sends: { url: string; body: unknown }[] = [];
    let releaseUpload: (value: unknown) => void = () => {};
    const held = new Promise((resolve) => { releaseUpload = resolve; });
    server.use(
      http.post('/api/v1/orgs/alpha/artifacts', async ({ request }) => {
        await held;
        return HttpResponse.json({ name: requestedName(request), size_bytes: 3, modified_at: 'now' });
      }),
      http.post('/api/v1/orgs/alpha/threads/THR-001/send', async ({ request }) => {
        sends.push({ url: new URL(request.url).pathname, body: await request.json() });
        return HttpResponse.json({ thread_id: 'x', seq: 2 });
      }),
    );

    const user = userEvent.setup();
    const view = renderWithProviders(<AppRoutes />, { route: '/orgs/alpha/threads/THR-001' });
    const composer = await screen.findByLabelText(/Compose follow-up/i);
    await user.type(composer, 'unmount draft');
    // Persist the draft on its debounce before unmounting.
    await waitFor(
      () => expect(localStorage.getItem('happyranch:draft:alpha:THR-001')).toBe('unmount draft'),
      { timeout: 1500 },
    );
    await user.upload(
      screen.getByLabelText(/Attach files/i),
      new File(['abc'], 'note.txt', { type: 'text/plain' }),
    );
    fireEvent.click(screen.getByRole('button', { name: /^Send$/i }));

    // Full unmount while the upload is held discards the pending chip; the
    // draft survives only through its debounced localStorage write.
    view.unmount();

    renderWithProviders(<AppRoutes />, { route: '/orgs/alpha/threads/THR-001' });
    const remounted = await screen.findByLabelText(/Compose follow-up/i);
    expect(screen.queryByText('note.txt')).toBeNull();
    expect((remounted as HTMLTextAreaElement).value).toBe('unmount draft');

    // The captured submission still completes exactly once for its original
    // destination — no duplicate send, no state write into the dead view.
    releaseUpload(undefined);
    await waitFor(() => expect(sends).toHaveLength(1));
    await new Promise((resolve) => setTimeout(resolve, 350));
    expect(sends).toHaveLength(1);
  });

  test('the draft is keyed by org AND thread and persists on the 300 ms debounce (C8.5)', async () => {
    sessionStorage.setItem('happyranch.token', 'tok');
    server.use(...orgHandlers('alpha', ['THR-001']));
    const user = userEvent.setup();
    renderWithProviders(<AppRoutes />, { route: '/orgs/alpha/threads/THR-001' });
    const composer = await screen.findByLabelText(/Compose follow-up/i);
    await user.type(composer, 'debounced');
    // Not yet written before the debounce settles.
    expect(localStorage.getItem('happyranch:draft:alpha:THR-001')).toBeNull();
    await waitFor(
      () => expect(localStorage.getItem('happyranch:draft:alpha:THR-001')).toBe('debounced'),
      { timeout: 1500 },
    );
  });
});
