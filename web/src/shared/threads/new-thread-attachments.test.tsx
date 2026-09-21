/**
 * TASK-8599 — NewThreadDialog attachment cases (accepted case-design C1.3, C2
 * new-thread variant, C8.3). Real `<AppProvider>` + router + MSW boundary.
 */
import { fireEvent, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { http, HttpResponse } from 'msw';
import { useNavigate, useLocation } from 'react-router-dom';
import { beforeEach, describe, expect, test, vi } from 'vitest';
import { AppRoutes } from '@/routes';
import { renderWithProviders } from '@/test/render';
import { server } from '@/test/server';

const SLUG = 'alpha';

function NavTo({ to, label, testId }: { to: string; label: string; testId?: string }) {
  const navigate = useNavigate();
  return (
    <button type="button" data-testid={testId} onClick={() => navigate(to)}>
      {label}
    </button>
  );
}

function LocationProbe() {
  const location = useLocation();
  return <span data-testid="review-location">{location.pathname}</span>;
}

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

function stubCreatedThread(threadId: string) {
  server.use(
    http.get(`/api/v1/orgs/${SLUG}/threads/${threadId}`, () =>
      HttpResponse.json({
        thread_id: threadId,
        subject: 'Created',
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
    http.get(`/api/v1/orgs/${SLUG}/threads/${threadId}/messages`, () =>
      HttpResponse.json({ messages: [] }),
    ),
    http.get(`/api/v1/orgs/${SLUG}/threads/${threadId}/tail`, () =>
      HttpResponse.text('', { headers: { 'content-type': 'text/event-stream' } }),
    ),
  );
}

function requestedName(request: Request): string {
  return new URL(request.url).searchParams.get('name') ?? '';
}

type CapturedPart = { field: string; filename: string; originalName: string; size: number };

/** Observe the real FormData preparation seam (actual File name + size). */
function captureFormParts() {
  const parts: CapturedPart[] = [];
  const originalSet = FormData.prototype.set;
  const spy = vi
    .spyOn(FormData.prototype, 'set')
    .mockImplementation(function (this: FormData, field: string, value: unknown, filename?: string) {
      if (value instanceof File) {
        parts.push({
          field,
          filename: filename ?? value.name,
          originalName: value.name,
          size: value.size,
        });
      }
      return originalSet.call(this, field, value as Blob, filename);
    });
  return { parts, restore: () => spy.mockRestore() };
}

async function openDialog(user: ReturnType<typeof userEvent.setup>) {
  await user.click(await screen.findByRole('button', { name: /New thread/i }));
}

async function fillDialog(
  user: ReturnType<typeof userEvent.setup>,
  subject: string,
  body?: string,
) {
  await user.type(screen.getByLabelText(/^Subject$/i), subject);
  await user.type(screen.getByLabelText(/^Recipients/i), 'agent_a');
  if (body) await user.type(screen.getByLabelText(/^Body \(Markdown\)$/i), body);
}

beforeEach(() => {
  localStorage.clear();
});

describe('NewThreadDialog attachments', () => {
  test('uploads the file before a JSON compose and passes the ref (C1.3)', async () => {
    sessionStorage.setItem('happyranch.token', 'tok');
    stubBaseHandlers();
    stubCreatedThread('THR-NEW');
    const order: string[] = [];
    let composeBody: unknown = null;
    server.use(
      http.post(`/api/v1/orgs/${SLUG}/artifacts`, async ({ request }) => {
        order.push('upload');
        return HttpResponse.json({ name: requestedName(request), size_bytes: 3, modified_at: 'now' });
      }),
      http.post(`/api/v1/orgs/${SLUG}/threads`, async ({ request }) => {
        order.push('compose');
        composeBody = await request.json();
        return HttpResponse.json(
          { thread_id: 'THR-NEW', started_at: 'now', pending_replies: 1 },
          { status: 201 },
        );
      }),
    );

    const user = userEvent.setup();
    renderWithProviders(<AppRoutes />, { route: `/orgs/${SLUG}/threads` });
    await openDialog(user);
    await user.type(screen.getByLabelText(/^Subject$/i), 'Hi');
    await user.type(screen.getByLabelText(/^Recipients/i), 'agent_a');
    await user.type(screen.getByLabelText(/^Body \(Markdown\)$/i), 'Hello');
    await user.upload(
      screen.getByLabelText(/Attach files/i),
      new File(['pdf'], 'report.pdf', { type: 'application/pdf' }),
    );
    await user.click(screen.getByRole('button', { name: /^Send$/i }));

    await waitFor(() => expect(composeBody).not.toBeNull());
    expect(order).toEqual(['upload', 'compose']);
    expect(composeBody).toMatchObject({
      subject: 'Hi',
      recipients: ['agent_a'],
      body_markdown: 'Hello',
      attachments: [
        {
          display_name: 'report.pdf',
          content_type: 'application/pdf',
        },
      ],
    });
  });

  test('a 413 upload rejection sends no compose, keeps the dialog open and retains the File (C2.4 new-thread)', async () => {
    sessionStorage.setItem('happyranch.token', 'tok');
    stubBaseHandlers();
    let composeCount = 0;
    const { parts, restore } = captureFormParts();
    server.use(
      http.post(`/api/v1/orgs/${SLUG}/artifacts`, () =>
        HttpResponse.json(
          { detail: { code: 'artifact_too_large', max_bytes: 10, size_bytes: 11 } },
          { status: 413 },
        ),
      ),
      http.post(`/api/v1/orgs/${SLUG}/threads`, () => {
        composeCount += 1;
        return HttpResponse.json(
          { thread_id: 'THR-NEW', started_at: 'now', pending_replies: 1 },
          { status: 201 },
        );
      }),
    );

    try {
      const user = userEvent.setup();
      renderWithProviders(<AppRoutes />, { route: `/orgs/${SLUG}/threads` });
      await openDialog(user);
      await user.type(screen.getByLabelText(/^Subject$/i), 'Hi');
      await user.type(screen.getByLabelText(/^Recipients/i), 'agent_a');
      await user.upload(
        screen.getByLabelText(/Attach files/i),
        new File(['0123456789'], 'big.txt', { type: 'text/plain' }),
      );
      await user.click(screen.getByRole('button', { name: /^Send$/i }));

      const error = await screen.findByText(/too large to upload/i);
      expect(error.textContent).toContain('big.txt');
      expect(composeCount).toBe(0);
      expect(screen.getByRole('dialog')).toBeInTheDocument();
      expect(parts).toHaveLength(1);
      expect(parts[0].originalName).toBe('big.txt');
      expect(parts[0].size).toBe(10);
      expect(screen.getAllByText(/big\.txt/).length).toBeGreaterThanOrEqual(1);
      expect(screen.getByLabelText(/^Subject$/i)).toHaveValue('Hi');
      expect(screen.getByLabelText(/Attach files/i)).not.toBeDisabled();
      expect(screen.getByRole('button', { name: 'Remove attachment' })).not.toBeDisabled();
      await waitFor(() =>
        expect(screen.getByRole('button', { name: /^Send$/i })).not.toBeDisabled(),
      );
    } finally {
      restore();
    }
  });

  test('a closed/reopened dialog is not closed by the abandoned submission (C8.3)', async () => {
    sessionStorage.setItem('happyranch.token', 'tok');
    stubBaseHandlers();
    stubCreatedThread('THR-NEW');
    let composeCount = 0;
    let releaseUpload: (value: unknown) => void = () => {};
    const held = new Promise((resolve) => { releaseUpload = resolve; });
    server.use(
      http.post(`/api/v1/orgs/${SLUG}/artifacts`, async ({ request }) => {
        await held;
        return HttpResponse.json({ name: requestedName(request), size_bytes: 3, modified_at: 'now' });
      }),
      http.post(`/api/v1/orgs/${SLUG}/threads`, () => {
        composeCount += 1;
        return HttpResponse.json(
          { thread_id: 'THR-NEW', started_at: 'now', pending_replies: 1 },
          { status: 201 },
        );
      }),
    );

    const user = userEvent.setup();
    renderWithProviders(<AppRoutes />, { route: `/orgs/${SLUG}/threads` });
    await openDialog(user);
    await user.type(screen.getByLabelText(/^Subject$/i), 'First');
    await user.type(screen.getByLabelText(/^Recipients/i), 'agent_a');
    await user.upload(
      screen.getByLabelText(/Attach files/i),
      new File(['abc'], 'note.txt', { type: 'text/plain' }),
    );
    await user.click(screen.getByRole('button', { name: /^Send$/i }));
    // Close while the upload is held, then start a fresh dialog.
    await user.click(screen.getByRole('button', { name: /^Cancel$/i }));
    await openDialog(user);
    await user.type(screen.getByLabelText(/^Subject$/i), 'Second');
    await user.type(screen.getByLabelText(/^Recipients/i), 'agent_a');
    releaseUpload(undefined);

    await waitFor(() => expect(composeCount).toBe(1));
    expect(screen.getByRole('dialog')).toBeInTheDocument();
    expect(screen.getByLabelText(/^Subject$/i)).toHaveValue('Second');
  });
});

describe('NewThreadDialog — remaining failure seams, retry and abandonment (TASK-8616)', () => {
  test('an invoked preparation failure fetches nothing and names the file (C2.1 new-thread)', async () => {
    sessionStorage.setItem('happyranch.token', 'tok');
    stubBaseHandlers();
    let artifactFetches = 0;
    let composeCount = 0;
    server.use(
      http.post(`/api/v1/orgs/${SLUG}/artifacts`, () => {
        artifactFetches += 1;
        return HttpResponse.json({ name: 'x', size_bytes: 1, modified_at: 'now' });
      }),
      http.post(`/api/v1/orgs/${SLUG}/threads`, () => {
        composeCount += 1;
        return HttpResponse.json({ thread_id: 'THR-NEW', started_at: 'now', pending_replies: 1 });
      }),
    );
    let capturedName = '';
    let capturedSize = -1;
    const setSpy = vi.spyOn(FormData.prototype, 'set').mockImplementationOnce(function (
      _field: string,
      value: string | Blob,
      _filename?: string,
    ) {
      if (value instanceof File) {
        capturedName = value.name;
        capturedSize = value.size;
      }
      throw new Error('simulated preparation failure');
    });
    try {
      const user = userEvent.setup();
      renderWithProviders(<AppRoutes />, { route: `/orgs/${SLUG}/threads` });
      await openDialog(user);
      await fillDialog(user, 'Hi');
      await user.upload(
        screen.getByLabelText(/Attach files/i),
        new File(['abc'], 'prep.txt', { type: 'text/plain' }),
      );
      await user.click(screen.getByRole('button', { name: /^Send$/i }));
      const error = await screen.findByText(/simulated preparation failure/i);
      expect(error.textContent).toContain('prep.txt');
      expect(artifactFetches).toBe(0);
      expect(composeCount).toBe(0);
      expect(capturedName).toBe('prep.txt');
      expect(capturedSize).toBe(3);
      expect(screen.getAllByText(/prep\.txt/).length).toBeGreaterThanOrEqual(1);
      expect(screen.getByLabelText(/^Subject$/i)).toHaveValue('Hi');
      // Controls are released for a retry.
      await waitFor(() =>
        expect(screen.getByRole('button', { name: /^Send$/i })).not.toBeDisabled(),
      );
      expect(screen.getByLabelText(/Attach files/i)).not.toBeDisabled();
      expect(screen.getByRole('button', { name: 'Remove attachment' })).not.toBeDisabled();
    } finally {
      setSpy.mockRestore();
    }
  });

  test('a transport-unknown upload attempts once, never composes and retains the File (C2.2 new-thread)', async () => {
    sessionStorage.setItem('happyranch.token', 'tok');
    stubBaseHandlers();
    let uploadCount = 0;
    let composeCount = 0;
    const { parts, restore } = captureFormParts();
    server.use(
      http.post(`/api/v1/orgs/${SLUG}/artifacts`, () => {
        uploadCount += 1;
        return HttpResponse.error();
      }),
      http.post(`/api/v1/orgs/${SLUG}/threads`, () => {
        composeCount += 1;
        return HttpResponse.json({ thread_id: 'THR-NEW', started_at: 'now', pending_replies: 1 });
      }),
    );
    try {
      const user = userEvent.setup();
      renderWithProviders(<AppRoutes />, { route: `/orgs/${SLUG}/threads` });
      await openDialog(user);
      await fillDialog(user, 'Hi');
      await user.upload(
        screen.getByLabelText(/Attach files/i),
        new File(['abc'], 'note.txt', { type: 'text/plain' }),
      );
      await user.click(screen.getByRole('button', { name: /^Send$/i }));
      await screen.findByText(/Failed to fetch/i);
      expect(uploadCount).toBe(1);
      expect(composeCount).toBe(0);
      expect(screen.getAllByText(/note\.txt/).length).toBeGreaterThanOrEqual(1);
      expect(parts).toHaveLength(1);
      expect(parts[0].originalName).toBe('note.txt');
      expect(parts[0].size).toBe(3);
      expect(screen.getByLabelText(/^Subject$/i)).toHaveValue('Hi');
      await waitFor(() =>
        expect(screen.getByRole('button', { name: /^Send$/i })).not.toBeDisabled(),
      );
      expect(screen.getByLabelText(/Attach files/i)).not.toBeDisabled();
      expect(screen.getByRole('button', { name: 'Remove attachment' })).not.toBeDisabled();
    } finally {
      restore();
    }
  });

  test('a 400 upload rejection maps the code, keeps the dialog open and retains the File (C2.3 new-thread)', async () => {
    sessionStorage.setItem('happyranch.token', 'tok');
    stubBaseHandlers();
    let composeCount = 0;
    const { parts, restore } = captureFormParts();
    server.use(
      http.post(`/api/v1/orgs/${SLUG}/artifacts`, () =>
        HttpResponse.json({ detail: { code: 'invalid_artifact_name' } }, { status: 400 }),
      ),
      http.post(`/api/v1/orgs/${SLUG}/threads`, () => {
        composeCount += 1;
        return HttpResponse.json({ thread_id: 'THR-NEW', started_at: 'now', pending_replies: 1 });
      }),
    );
    try {
      const user = userEvent.setup();
      renderWithProviders(<AppRoutes />, { route: `/orgs/${SLUG}/threads` });
      await openDialog(user);
      await fillDialog(user, 'Hi');
      await user.upload(
        screen.getByLabelText(/Attach files/i),
        new File(['abc'], 'weird?.txt', { type: 'text/plain' }),
      );
      await user.click(screen.getByRole('button', { name: /^Send$/i }));
      const error = await screen.findByText(/name is not allowed/i);
      expect(error.textContent).toContain('weird?.txt');
      expect(composeCount).toBe(0);
      expect(screen.getByRole('dialog')).toBeInTheDocument();
      expect(screen.getAllByText(/weird\?\.txt/).length).toBeGreaterThanOrEqual(1);
      expect(parts).toHaveLength(1);
      expect(parts[0].originalName).toBe('weird?.txt');
      expect(parts[0].size).toBe(3);
      expect(screen.getByLabelText(/^Subject$/i)).toHaveValue('Hi');
      await waitFor(() =>
        expect(screen.getByRole('button', { name: /^Send$/i })).not.toBeDisabled(),
      );
      expect(screen.getByLabelText(/Attach files/i)).not.toBeDisabled();
      expect(screen.getByRole('button', { name: 'Remove attachment' })).not.toBeDisabled();
    } finally {
      restore();
    }
  });

  test('a retained selection is not re-uploaded on retry and both refs compose (C3.1 new-thread)', async () => {
    sessionStorage.setItem('happyranch.token', 'tok');
    stubBaseHandlers();
    stubCreatedThread('THR-NEW');
    const uploadedNames: string[] = [];
    let composeBody: unknown = null;
    let composeCount = 0;
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
      http.post(`/api/v1/orgs/${SLUG}/threads`, async ({ request }) => {
        composeCount += 1;
        composeBody = await request.json();
        return HttpResponse.json(
          { thread_id: 'THR-NEW', started_at: 'now', pending_replies: 1 },
          { status: 201 },
        );
      }),
    );
    const user = userEvent.setup();
    renderWithProviders(<AppRoutes />, { route: `/orgs/${SLUG}/threads` });
    await openDialog(user);
    await fillDialog(user, 'Hi');
    await user.upload(screen.getByLabelText(/Attach files/i), [
      new File(['aaa'], 'a.txt', { type: 'text/plain' }),
      new File(['bbb'], 'b.txt', { type: 'text/plain' }),
    ]);
    await user.click(screen.getByRole('button', { name: /^Send$/i }));
    await screen.findByText(/Failed to fetch/i);
    expect(composeCount).toBe(0);
    await user.click(screen.getByRole('button', { name: /^Send$/i }));
    await waitFor(() => expect(composeCount).toBe(1));
    expect(uploadedNames).toHaveLength(3);
    const refs = (composeBody as { attachments: { display_name: string }[] }).attachments;
    expect(refs.map((r) => r.display_name)).toEqual(['a.txt', 'b.txt']);
  });

  test('three new-thread files with the middle failing once make 4 uploads and one compose [A,B,C] (C3.2 new-thread)', async () => {
    sessionStorage.setItem('happyranch.token', 'tok');
    stubBaseHandlers();
    stubCreatedThread('THR-NEW');
    const uploadedNames: string[] = [];
    let composeBody: unknown = null;
    let composeCount = 0;
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
      http.post(`/api/v1/orgs/${SLUG}/threads`, async ({ request }) => {
        composeCount += 1;
        composeBody = await request.json();
        return HttpResponse.json(
          { thread_id: 'THR-NEW', started_at: 'now', pending_replies: 1 },
          { status: 201 },
        );
      }),
    );
    const user = userEvent.setup();
    renderWithProviders(<AppRoutes />, { route: `/orgs/${SLUG}/threads` });
    await openDialog(user);
    await fillDialog(user, 'Hi');
    await user.upload(screen.getByLabelText(/Attach files/i), [
      new File(['aaa'], 'a.txt', { type: 'text/plain' }),
      new File(['bbb'], 'b.txt', { type: 'text/plain' }),
      new File(['ccc'], 'c.txt', { type: 'text/plain' }),
    ]);
    await user.click(screen.getByRole('button', { name: /^Send$/i }));
    await screen.findByText(/Failed to fetch/i);
    // Zero compose on the failed attempt; the failed selection is retained.
    expect(composeCount).toBe(0);
    expect(screen.getAllByText(/b\.txt/).length).toBeGreaterThanOrEqual(1);
    await user.click(screen.getByRole('button', { name: /^Send$/i }));
    await waitFor(() => expect(composeCount).toBe(1));
    expect(uploadedNames).toHaveLength(4);
    const refs = (composeBody as { attachments: { artifact_name: string; display_name: string }[] }).attachments;
    // Exact retained-ref identity: A reused, B retried under its retained name,
    // C uploaded once — one compose carrying all three in selection order.
    expect(refs.map((r) => r.artifact_name)).toEqual([
      uploadedNames[0],
      uploadedNames[2],
      uploadedNames[3],
    ]);
    expect(refs.map((r) => r.display_name)).toEqual(['a.txt', 'b.txt', 'c.txt']);
  });

  test('new-thread removal invalidates only its own selection and preserves the completed ref (C3.4 new-thread)', async () => {
    sessionStorage.setItem('happyranch.token', 'tok');
    stubBaseHandlers();
    stubCreatedThread('THR-NEW');
    const uploadedNames: string[] = [];
    let composeBody: unknown = null;
    let composeCount = 0;
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
      http.post(`/api/v1/orgs/${SLUG}/threads`, async ({ request }) => {
        composeCount += 1;
        composeBody = await request.json();
        return HttpResponse.json(
          { thread_id: 'THR-NEW', started_at: 'now', pending_replies: 1 },
          { status: 201 },
        );
      }),
    );
    const user = userEvent.setup();
    renderWithProviders(<AppRoutes />, { route: `/orgs/${SLUG}/threads` });
    await openDialog(user);
    await fillDialog(user, 'Hi');
    await user.upload(screen.getByLabelText(/Attach files/i), [
      new File(['aaa'], 'a.txt', { type: 'text/plain' }),
      new File(['bbb'], 'b.txt', { type: 'text/plain' }),
    ]);
    await user.click(screen.getByRole('button', { name: /^Send$/i }));
    await screen.findByText(/Failed to fetch/i);
    expect(composeCount).toBe(0);
    // Remove the failed B (no request), add replacement C, retry.
    const removeButtons = screen.getAllByRole('button', { name: 'Remove attachment' });
    await user.click(removeButtons[1]);
    await user.upload(
      screen.getByLabelText(/Attach files/i),
      new File(['ccc'], 'c.txt', { type: 'text/plain' }),
    );
    await user.click(screen.getByRole('button', { name: /^Send$/i }));
    await waitFor(() => expect(composeCount).toBe(1));
    expect(uploadedNames).toHaveLength(3);
    const refs = (composeBody as { attachments: { artifact_name: string; display_name: string }[] }).attachments;
    expect(refs.map((r) => r.display_name)).toEqual(['a.txt', 'c.txt']);
    expect(refs[0].artifact_name).toBe(uploadedNames[0]);
  });

  test('a 404 artifact_not_found compose keeps the refs until remove/reselect/reupload (C4.2 new-thread)', async () => {
    sessionStorage.setItem('happyranch.token', 'tok');
    stubBaseHandlers();
    stubCreatedThread('THR-NEW');
    const uploadedNames: string[] = [];
    let composeBody: unknown = null;
    let composeCount = 0;
    server.use(
      http.post(`/api/v1/orgs/${SLUG}/artifacts`, async ({ request }) => {
        const name = requestedName(request);
        uploadedNames.push(name);
        return HttpResponse.json({ name, size_bytes: 3, modified_at: 'now' });
      }),
      http.post(`/api/v1/orgs/${SLUG}/threads`, async ({ request }) => {
        composeCount += 1;
        composeBody = await request.json();
        if (composeCount <= 2) {
          return HttpResponse.json({ detail: { code: 'artifact_not_found' } }, { status: 404 });
        }
        return HttpResponse.json(
          { thread_id: 'THR-NEW', started_at: 'now', pending_replies: 1 },
          { status: 201 },
        );
      }),
    );
    const user = userEvent.setup();
    renderWithProviders(<AppRoutes />, { route: `/orgs/${SLUG}/threads` });
    await openDialog(user);
    await fillDialog(user, 'Hi');
    await user.upload(screen.getByLabelText(/Attach files/i), [
      new File(['aaa'], 'a.txt', { type: 'text/plain' }),
      new File(['bbb'], 'b.txt', { type: 'text/plain' }),
    ]);
    await user.click(screen.getByRole('button', { name: /^Send$/i }));
    const error = await screen.findByText(/no longer available; remove it and attach it again/i);
    // A compose rejection must not be labelled with the last uploaded file.
    expect(error.textContent?.startsWith('b.txt:')).toBe(false);
    expect(composeCount).toBe(1);
    expect(uploadedNames).toHaveLength(2);
    expect(screen.getByRole('dialog')).toBeInTheDocument();
    expect(screen.getByLabelText(/^Subject$/i)).toHaveValue('Hi');
    expect(screen.getAllByRole('button', { name: 'Remove attachment' })).toHaveLength(2);
    await waitFor(() => expect(screen.getByRole('button', { name: /^Send$/i })).not.toBeDisabled());

    // Retry unchanged: the missing ref persists and reuses both completed refs.
    await user.click(screen.getByRole('button', { name: /^Send$/i }));
    await waitFor(() => expect(composeCount).toBe(2));
    expect(uploadedNames).toHaveLength(2);

    // Remove B, reselect/reupload it, then retry succeeds with A's ref intact.
    await user.click(screen.getAllByRole('button', { name: 'Remove attachment' })[1]);
    await user.upload(
      screen.getByLabelText(/Attach files/i),
      new File(['bbb2'], 'b.txt', { type: 'text/plain' }),
    );
    await user.click(screen.getByRole('button', { name: /^Send$/i }));
    await waitFor(() => expect(composeCount).toBe(3));
    expect(uploadedNames).toHaveLength(3);
    const refs = (composeBody as { attachments: { artifact_name: string }[] }).attachments;
    expect(refs.map((r) => r.artifact_name)).toEqual([uploadedNames[0], uploadedNames[2]]);
  });

  test('closing without reopening abandons the dialog but the compose still finishes once (C8.3b)', async () => {
    sessionStorage.setItem('happyranch.token', 'tok');
    stubBaseHandlers();
    stubCreatedThread('THR-NEW');
    let composeCount = 0;
    let releaseUpload: (value: unknown) => void = () => {};
    const held = new Promise((resolve) => { releaseUpload = resolve; });
    server.use(
      http.post(`/api/v1/orgs/${SLUG}/artifacts`, async ({ request }) => {
        await held;
        return HttpResponse.json({ name: requestedName(request), size_bytes: 3, modified_at: 'now' });
      }),
      http.post(`/api/v1/orgs/${SLUG}/threads`, () => {
        composeCount += 1;
        return HttpResponse.json(
          { thread_id: 'THR-NEW', started_at: 'now', pending_replies: 1 },
          { status: 201 },
        );
      }),
    );
    const user = userEvent.setup();
    renderWithProviders(<AppRoutes />, { route: `/orgs/${SLUG}/threads` });
    await openDialog(user);
    await fillDialog(user, 'Abandoned');
    await user.upload(
      screen.getByLabelText(/Attach files/i),
      new File(['abc'], 'note.txt', { type: 'text/plain' }),
    );
    await user.click(screen.getByRole('button', { name: /^Send$/i }));
    // Close and DO NOT reopen.
    await user.click(screen.getByRole('button', { name: /^Cancel$/i }));
    expect(screen.queryByLabelText(/^Subject$/i)).toBeNull();
    expect(screen.queryByRole('button', { name: /^Cancel$/i })).toBeNull();
    releaseUpload(undefined);
    // The captured submission still finishes for its destination, but the
    // departed dialog must not be closed, navigated or reset by it.
    await waitFor(() => expect(composeCount).toBe(1));
    await new Promise((resolve) => setTimeout(resolve, 100));
    expect(screen.queryByLabelText(/^Subject$/i)).toBeNull();
  });

  test('an org switch during upload keeps the compose at its captured org (C8.2b)', async () => {
    sessionStorage.setItem('happyranch.token', 'tok');
    server.use(
      http.get('/api/v1/orgs', () =>
        HttpResponse.json({
          orgs: [
            { slug: 'alpha', root: '/a' },
            { slug: 'beta', root: '/b' },
          ],
        }),
      ),
      ...['alpha', 'beta'].flatMap((org) => [
        http.get(`/api/v1/orgs/${org}/agents`, () => HttpResponse.json({ agents: [] })),
        http.get(`/api/v1/orgs/${org}/threads`, () => HttpResponse.json({ threads: [] })),
        http.get(`/api/v1/orgs/${org}/threads/events`, () =>
          HttpResponse.text('', { headers: { 'content-type': 'text/event-stream' } }),
        ),
        http.get(`/api/v1/orgs/${org}/tokens`, () => HttpResponse.json({ rollup: [] })),
      ]),
    );
    const composeUrls: string[] = [];
    let composeBody: unknown = null;
    let releaseUpload: (value: unknown) => void = () => {};
    const held = new Promise((resolve) => { releaseUpload = resolve; });
    server.use(
      http.post(`/api/v1/orgs/${SLUG}/artifacts`, async ({ request }) => {
        await held;
        return HttpResponse.json({ name: requestedName(request), size_bytes: 3, modified_at: 'now' });
      }),
      http.post(`/api/v1/orgs/${SLUG}/threads`, async ({ request }) => {
        composeUrls.push(new URL(request.url).pathname);
        composeBody = await request.json();
        return HttpResponse.json(
          { thread_id: 'THR-NEW', started_at: 'now', pending_replies: 1 },
          { status: 201 },
        );
      }),
      http.post('/api/v1/orgs/beta/threads', async ({ request }) => {
        composeUrls.push(new URL(request.url).pathname);
        return HttpResponse.json(
          { thread_id: 'THR-BETA', started_at: 'now', pending_replies: 1 },
          { status: 201 },
        );
      }),
      http.get('/api/v1/orgs/alpha/threads/THR-NEW', () =>
        HttpResponse.json({
          thread_id: 'THR-NEW',
          subject: 'Hi',
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
    );
    const user = userEvent.setup();
    renderWithProviders(
      <>
        <AppRoutes />
        <NavTo to="/orgs/beta/threads" label="nav-beta-org" testId="nav-beta-org" />
        <LocationProbe />
      </>,
      { route: `/orgs/${SLUG}/threads` },
    );
    await openDialog(user);
    await fillDialog(user, 'Captured subject', 'Captured body');
    await user.upload(
      screen.getByLabelText(/Attach files/i),
      new File(['abc'], 'note.txt', { type: 'text/plain' }),
    );
    await user.click(screen.getByRole('button', { name: /^Send$/i }));
    // Switch org while the upload is held. The modal dialog marks the rest of
    // the page aria-hidden, so dispatch the navigation directly.
    fireEvent.click(document.querySelector('[data-testid="nav-beta-org"]') as HTMLElement);
    await waitFor(() =>
      expect(screen.getByTestId('review-location')).toHaveTextContent('/orgs/beta/threads'),
    );
    releaseUpload(undefined);
    await waitFor(() => expect(composeUrls).toHaveLength(1));
    // The compose URL stays at the org captured at first submit.
    expect(composeUrls[0]).toBe(`/api/v1/orgs/${SLUG}/threads`);
    // Org departure invalidates result ownership: the late alpha success must
    // NOT navigate the beta view back to alpha's created thread.
    await new Promise((resolve) => setTimeout(resolve, 100));
    expect(screen.getByTestId('review-location').textContent).toBe('/orgs/beta/threads');
    expect(screen.getByRole('dialog')).toBeInTheDocument();
    expect(composeBody).toMatchObject({
      subject: 'Captured subject',
      recipients: ['agent_a'],
      body_markdown: 'Captured body',
    });
  });
});
