/**
 * TASK-8599 — focused shipping-seam cases for the accepted attachment
 * lifecycle repair (TASK-8596 case-design Groups 2/3/5/8).
 *
 * These run through the real `<AppProvider>` + router + MSW fetch boundary
 * (`renderWithProviders`). MSW is browser-lifecycle evidence only; persisted
 * commit claims live in the task-owned backend probe.
 */
import { fireEvent, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { http, HttpResponse, type HttpResponseResolver } from 'msw';
import { useNavigate } from 'react-router-dom';
import { describe, expect, test, vi } from 'vitest';
import { AppRoutes } from '@/routes';
import { renderWithProviders } from '@/test/render';
import { server } from '@/test/server';

const SLUG = 'alpha';

function stubBaseHandlers() {
  server.use(
    http.get('/api/v1/orgs', () => HttpResponse.json({ orgs: [{ slug: SLUG, root: '/x' }] })),
    http.get(`/api/v1/orgs/${SLUG}/agents`, () => HttpResponse.json({ agents: [] })),
    http.get(`/api/v1/orgs/${SLUG}/threads`, () => HttpResponse.json({ threads: [] })),
    http.get(`/api/v1/orgs/${SLUG}/threads/events`, () =>
      HttpResponse.text('', { headers: { 'content-type': 'text/event-stream' } }),
    ),
    http.get(`/api/v1/orgs/${SLUG}/tokens`, () => HttpResponse.json({ rollup: [] })),
  );
}

function stubThread(threadId: string, status = 'open') {
  server.use(
    http.get(`/api/v1/orgs/${SLUG}/threads/${threadId}`, () =>
      HttpResponse.json({
        thread_id: threadId,
        subject: `Subject ${threadId}`,
        status,
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
    http.get(`/api/v1/orgs/${SLUG}/threads/${threadId}/messages`, () =>
      HttpResponse.json({ messages: [] }),
    ),
    http.get(`/api/v1/orgs/${SLUG}/threads/${threadId}/tail`, () =>
      HttpResponse.text('', { headers: { 'content-type': 'text/event-stream' } }),
    ),
  );
}

/** The artifact upload carries the generated name in the `name` query param. */
function requestedName(request: Request): string {
  return new URL(request.url).searchParams.get('name') ?? '';
}

function sendRecorder(sink: { url: string; body: unknown }[]): HttpResponseResolver {
  return async ({ request }) => {
    sink.push({ url: new URL(request.url).pathname, body: await request.json() });
    return HttpResponse.json({ thread_id: 'x', seq: 2 });
  };
}

type SendAttachment = { artifact_name: string; display_name: string; content_type: string | null };

function attachmentsOf(body: unknown): SendAttachment[] {
  return ((body as { attachments?: SendAttachment[] }).attachments ?? []);
}

describe('attachment lifecycle — one intentional submission (C5)', () => {
  test('double-click Send during a held upload issues exactly one upload and one send', async () => {
    sessionStorage.setItem('happyranch.token', 'tok');
    stubBaseHandlers();
    stubThread('THR-001');
    const sends: { url: string; body: unknown }[] = [];
    let uploadCount = 0;
    let releaseUpload: (value: unknown) => void = () => {};
    const held = new Promise((resolve) => { releaseUpload = resolve; });
    server.use(
      http.post(`/api/v1/orgs/${SLUG}/artifacts`, async ({ request }) => {
        uploadCount += 1;
        await held;
        return HttpResponse.json({ name: requestedName(request), size_bytes: 3, modified_at: 'now' });
      }),
      http.post(`/api/v1/orgs/${SLUG}/threads/THR-001/send`, sendRecorder(sends)),
    );

    const user = userEvent.setup();
    renderWithProviders(<AppRoutes />, { route: `/orgs/${SLUG}/threads/THR-001` });
    await user.upload(
      await screen.findByLabelText(/Attach files/i),
      new File(['abc'], 'note.txt', { type: 'text/plain' }),
    );
    const sendBtn = screen.getByRole('button', { name: /^Send$/i });
    fireEvent.click(sendBtn);
    fireEvent.click(sendBtn);
    releaseUpload(undefined);
    await waitFor(() => expect(sends).toHaveLength(1));
    expect(uploadCount).toBe(1);
  });

  test('Enter + Send in the same turn issues exactly one upload and one send (C5.2)', async () => {
    sessionStorage.setItem('happyranch.token', 'tok');
    stubBaseHandlers();
    stubThread('THR-001');
    const sends: { url: string; body: unknown }[] = [];
    let uploadCount = 0;
    let releaseUpload: (value: unknown) => void = () => {};
    const held = new Promise((resolve) => { releaseUpload = resolve; });
    server.use(
      http.post(`/api/v1/orgs/${SLUG}/artifacts`, async ({ request }) => {
        uploadCount += 1;
        await held;
        return HttpResponse.json({ name: requestedName(request), size_bytes: 3, modified_at: 'now' });
      }),
      http.post(`/api/v1/orgs/${SLUG}/threads/THR-001/send`, sendRecorder(sends)),
    );

    const user = userEvent.setup();
    renderWithProviders(<AppRoutes />, { route: `/orgs/${SLUG}/threads/THR-001` });
    const composer = await screen.findByLabelText(/Compose follow-up/i);
    await user.type(composer, 'hello');
    await user.upload(
      screen.getByLabelText(/Attach files/i),
      new File(['abc'], 'note.txt', { type: 'text/plain' }),
    );
    fireEvent.keyDown(composer, { key: 'Enter' });
    fireEvent.click(screen.getByRole('button', { name: /^Send$/i }));
    releaseUpload(undefined);
    await waitFor(() => expect(sends).toHaveLength(1));
    expect(uploadCount).toBe(1);
  });

  test('attach, send and remove controls are disabled while the upload is held (C5.4)', async () => {
    sessionStorage.setItem('happyranch.token', 'tok');
    stubBaseHandlers();
    stubThread('THR-001');
    const sends: { url: string; body: unknown }[] = [];
    let releaseUpload: (value: unknown) => void = () => {};
    const held = new Promise((resolve) => { releaseUpload = resolve; });
    server.use(
      http.post(`/api/v1/orgs/${SLUG}/artifacts`, async ({ request }) => {
        await held;
        return HttpResponse.json({ name: requestedName(request), size_bytes: 3, modified_at: 'now' });
      }),
      http.post(`/api/v1/orgs/${SLUG}/threads/THR-001/send`, sendRecorder(sends)),
    );

    const user = userEvent.setup();
    renderWithProviders(<AppRoutes />, { route: `/orgs/${SLUG}/threads/THR-001` });
    await user.upload(
      await screen.findByLabelText(/Attach files/i),
      new File(['abc'], 'note.txt', { type: 'text/plain' }),
    );
    fireEvent.click(screen.getByRole('button', { name: /^Send$/i }));

    await waitFor(() =>
      expect(screen.getByRole('button', { name: /^Send$/i })).toBeDisabled(),
    );
    expect(screen.getByLabelText(/Attach files/i)).toBeDisabled();
    expect(screen.getByRole('button', { name: 'Remove attachment' })).toBeDisabled();
    // Drain the held submission so its late request cannot leak into the next test.
    releaseUpload(undefined);
    await waitFor(() => expect(sends).toHaveLength(1));
  });

  test('a successful send leaves no stuck latch for the next independent message (C5.5)', async () => {
    sessionStorage.setItem('happyranch.token', 'tok');
    stubBaseHandlers();
    stubThread('THR-001');
    const sends: { url: string; body: unknown }[] = [];
    server.use(
      http.post(`/api/v1/orgs/${SLUG}/artifacts`, async ({ request }) =>
        HttpResponse.json({ name: requestedName(request), size_bytes: 3, modified_at: 'now' }),
      ),
      http.post(`/api/v1/orgs/${SLUG}/threads/THR-001/send`, sendRecorder(sends)),
    );

    const user = userEvent.setup();
    renderWithProviders(<AppRoutes />, { route: `/orgs/${SLUG}/threads/THR-001` });
    const composer = await screen.findByLabelText(/Compose follow-up/i);
    await user.type(composer, 'first');
    await user.click(screen.getByRole('button', { name: /^Send$/i }));
    await waitFor(() => expect(sends).toHaveLength(1));
    await user.type(composer, 'second');
    await user.click(screen.getByRole('button', { name: /^Send$/i }));
    await waitFor(() => expect(sends).toHaveLength(2));
    expect((sends[1].body as { body_markdown: string }).body_markdown).toBe('second');
    expect(attachmentsOf(sends[1].body)).toHaveLength(0);
  });
});

describe('attachment lifecycle — partial upload and retry (C3)', () => {
  test('a retained selection is not re-uploaded on retry: 3 uploads, one send (C3.1)', async () => {
    sessionStorage.setItem('happyranch.token', 'tok');
    stubBaseHandlers();
    stubThread('THR-001');
    const sends: { url: string; body: unknown }[] = [];
    const uploadedNames: string[] = [];
    let bFailed = false;
    server.use(
      http.post(`/api/v1/orgs/${SLUG}/artifacts`, async ({ request }) => {
        const name = requestedName(request);
        uploadedNames.push(name);
        if (name.endsWith('b.txt') && !bFailed) {
          bFailed = true;
          return HttpResponse.error();
        }
        return HttpResponse.json({ name, size_bytes: 3, modified_at: 'now' });
      }),
      http.post(`/api/v1/orgs/${SLUG}/threads/THR-001/send`, sendRecorder(sends)),
    );

    const user = userEvent.setup();
    renderWithProviders(<AppRoutes />, { route: `/orgs/${SLUG}/threads/THR-001` });
    await user.upload(await screen.findByLabelText(/Attach files/i), [
      new File(['aaa'], 'a.txt', { type: 'text/plain' }),
      new File(['bbb'], 'b.txt', { type: 'text/plain' }),
    ]);
    await user.click(screen.getByRole('button', { name: /^Send$/i }));
    await screen.findByText(/Failed to fetch/i);
    expect(uploadedNames.length).toBeGreaterThanOrEqual(2);
    expect(sends).toHaveLength(0);

    await user.click(screen.getByRole('button', { name: /^Send$/i }));
    await waitFor(() => expect(sends).toHaveLength(1));
    expect(uploadedNames).toHaveLength(3);
    const refs = attachmentsOf(sends[0].body);
    expect(refs.map((r) => r.artifact_name)).toEqual([uploadedNames[0], uploadedNames[2]]);
  });

  test('fixed serial schedule A once / B twice / C once = 4 attempts, one send [A,B,C] (C3.2)', async () => {
    sessionStorage.setItem('happyranch.token', 'tok');
    stubBaseHandlers();
    stubThread('THR-001');
    const sends: { url: string; body: unknown }[] = [];
    const uploadedNames: string[] = [];
    let bFailed = false;
    server.use(
      http.post(`/api/v1/orgs/${SLUG}/artifacts`, async ({ request }) => {
        const name = requestedName(request);
        uploadedNames.push(name);
        if (name.endsWith('b.txt') && !bFailed) {
          bFailed = true;
          return HttpResponse.error();
        }
        return HttpResponse.json({ name, size_bytes: 3, modified_at: 'now' });
      }),
      http.post(`/api/v1/orgs/${SLUG}/threads/THR-001/send`, sendRecorder(sends)),
    );

    const user = userEvent.setup();
    renderWithProviders(<AppRoutes />, { route: `/orgs/${SLUG}/threads/THR-001` });
    await user.upload(await screen.findByLabelText(/Attach files/i), [
      new File(['aaa'], 'a.txt', { type: 'text/plain' }),
      new File(['bbb'], 'b.txt', { type: 'text/plain' }),
      new File(['ccc'], 'c.txt', { type: 'text/plain' }),
    ]);
    await user.click(screen.getByRole('button', { name: /^Send$/i }));
    await screen.findByText(/Failed to fetch/i);
    await user.click(screen.getByRole('button', { name: /^Send$/i }));
    await waitFor(() => expect(sends).toHaveLength(1));
    expect(uploadedNames).toHaveLength(4);
    const refs = attachmentsOf(sends[0].body);
    expect(refs.map((r) => r.artifact_name)).toEqual([
      uploadedNames[0],
      uploadedNames[2],
      uploadedNames[3],
    ]);
    expect(refs.map((r) => r.display_name)).toEqual(['a.txt', 'b.txt', 'c.txt']);
  });

  test('removing the failed selection keeps the completed ref and uploads only the replacement (C3.4)', async () => {
    sessionStorage.setItem('happyranch.token', 'tok');
    stubBaseHandlers();
    stubThread('THR-001');
    const sends: { url: string; body: unknown }[] = [];
    const uploadedNames: string[] = [];
    let bFailed = false;
    server.use(
      http.post(`/api/v1/orgs/${SLUG}/artifacts`, async ({ request }) => {
        const name = requestedName(request);
        uploadedNames.push(name);
        if (name.endsWith('b.txt') && !bFailed) {
          bFailed = true;
          return HttpResponse.error();
        }
        return HttpResponse.json({ name, size_bytes: 3, modified_at: 'now' });
      }),
      http.post(`/api/v1/orgs/${SLUG}/threads/THR-001/send`, sendRecorder(sends)),
    );

    const user = userEvent.setup();
    renderWithProviders(<AppRoutes />, { route: `/orgs/${SLUG}/threads/THR-001` });
    await user.upload(await screen.findByLabelText(/Attach files/i), [
      new File(['aaa'], 'a.txt', { type: 'text/plain' }),
      new File(['bbb'], 'b.txt', { type: 'text/plain' }),
    ]);
    await user.click(screen.getByRole('button', { name: /^Send$/i }));
    await screen.findByText(/Failed to fetch/i);
    // Removing the failed selection sends nothing and invalidates only its ref.
    const removeButtons = screen.getAllByRole('button', { name: 'Remove attachment' });
    await user.click(removeButtons[1]);
    await user.upload(
      screen.getByLabelText(/Attach files/i),
      new File(['ccc'], 'c.txt', { type: 'text/plain' }),
    );
    await user.click(screen.getByRole('button', { name: /^Send$/i }));
    await waitFor(() => expect(sends).toHaveLength(1));
    expect(uploadedNames).toHaveLength(3);
    const refs = attachmentsOf(sends[0].body);
    expect(refs.map((r) => r.display_name)).toEqual(['a.txt', 'c.txt']);
    expect(refs[0].artifact_name).toBe(uploadedNames[0]);
  });
});

describe('attachment lifecycle — file-specific failures surface on the existing error line (C2)', () => {
  test('a 413 upload rejection names the file, maps the code, and sends nothing (C2.4)', async () => {
    sessionStorage.setItem('happyranch.token', 'tok');
    stubBaseHandlers();
    stubThread('THR-001');
    const sends: { url: string; body: unknown }[] = [];
    server.use(
      http.post(`/api/v1/orgs/${SLUG}/artifacts`, () =>
        HttpResponse.json(
          { detail: { code: 'artifact_too_large', max_bytes: 10, size_bytes: 11 } },
          { status: 413 },
        ),
      ),
      http.post(`/api/v1/orgs/${SLUG}/threads/THR-001/send`, sendRecorder(sends)),
    );

    const user = userEvent.setup();
    renderWithProviders(<AppRoutes />, { route: `/orgs/${SLUG}/threads/THR-001` });
    await user.upload(
      await screen.findByLabelText(/Attach files/i),
      new File(['0123456789'], 'big.txt', { type: 'text/plain' }),
    );
    await user.click(screen.getByRole('button', { name: /^Send$/i }));
    const error = await screen.findByText(/too large to upload/i);
    expect(error.textContent).toContain('big.txt');
    expect(sends).toHaveLength(0);
    // The chip still carries the true File.name.
    expect(screen.getAllByText(/big\.txt/).length).toBeGreaterThanOrEqual(1);
  });

  test('an invoked preparation failure (FormData.set) fetches nothing and names the file (C2.1)', async () => {
    sessionStorage.setItem('happyranch.token', 'tok');
    stubBaseHandlers();
    stubThread('THR-001');
    const sends: { url: string; body: unknown }[] = [];
    let artifactFetches = 0;
    server.use(
      http.post(`/api/v1/orgs/${SLUG}/artifacts`, () => {
        artifactFetches += 1;
        return HttpResponse.json({ name: 'x', size_bytes: 1, modified_at: 'now' });
      }),
      http.post(`/api/v1/orgs/${SLUG}/threads/THR-001/send`, sendRecorder(sends)),
    );
    const setSpy = vi.spyOn(FormData.prototype, 'set').mockImplementationOnce(() => {
      throw new Error('simulated preparation failure');
    });

    const user = userEvent.setup();
    renderWithProviders(<AppRoutes />, { route: `/orgs/${SLUG}/threads/THR-001` });
    await user.upload(
      await screen.findByLabelText(/Attach files/i),
      new File(['abc'], 'prep.txt', { type: 'text/plain' }),
    );
    await user.click(screen.getByRole('button', { name: /^Send$/i }));
    const error = await screen.findByText(/simulated preparation failure/i);
    expect(error.textContent).toContain('prep.txt');
    expect(artifactFetches).toBe(0);
    expect(sends).toHaveLength(0);
    setSpy.mockRestore();
  });
});

describe('attachment lifecycle — captured destination (C8)', () => {
  test('a thread switch mid-upload does not retarget the in-flight send (C8.1)', async () => {
    sessionStorage.setItem('happyranch.token', 'tok');
    stubBaseHandlers();
    stubThread('THR-001');
    stubThread('THR-002');
    const sends: { url: string; body: unknown }[] = [];
    let releaseUpload: (value: unknown) => void = () => {};
    const held = new Promise((resolve) => { releaseUpload = resolve; });
    server.use(
      http.post(`/api/v1/orgs/${SLUG}/artifacts`, async ({ request }) => {
        await held;
        return HttpResponse.json({ name: requestedName(request), size_bytes: 3, modified_at: 'now' });
      }),
      http.post(`/api/v1/orgs/${SLUG}/threads/THR-001/send`, sendRecorder(sends)),
      http.post(`/api/v1/orgs/${SLUG}/threads/THR-002/send`, sendRecorder(sends)),
    );

    function NavToB() {
      const navigate = useNavigate();
      return (
        <button type="button" onClick={() => navigate(`/orgs/${SLUG}/threads/THR-002`)}>
          navigate-target
        </button>
      );
    }

    const user = userEvent.setup();
    renderWithProviders(
      <>
        <AppRoutes />
        <NavToB />
      </>,
      { route: `/orgs/${SLUG}/threads/THR-001` },
    );
    await user.upload(
      await screen.findByLabelText(/Attach files/i),
      new File(['abc'], 'note.txt', { type: 'text/plain' }),
    );
    fireEvent.click(screen.getByRole('button', { name: /^Send$/i }));
    // Switch to thread B while A's upload is held.
    await user.click(screen.getByRole('button', { name: 'navigate-target' }));
    const bComposer = await screen.findByLabelText(/Compose follow-up/i);
    await user.type(bComposer, 'B draft');
    releaseUpload(undefined);

    await waitFor(() => expect(sends).toHaveLength(1));
    expect(sends[0].url).toBe(`/api/v1/orgs/${SLUG}/threads/THR-001/send`);
    expect(sends.some((s) => s.url.endsWith('/THR-002/send'))).toBe(false);
    // The late success belongs to A and must not clear B's draft.
    await new Promise((resolve) => setTimeout(resolve, 350));
    expect((screen.getByLabelText(/Compose follow-up/i) as HTMLTextAreaElement).value).toBe('B draft');
  });
});
