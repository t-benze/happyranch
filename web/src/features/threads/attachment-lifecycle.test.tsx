/**
 * TASK-8599 — focused shipping-seam cases for the accepted attachment
 * lifecycle repair (TASK-8596 case-design Groups 2/3/5/8).
 *
 * These run through the real `<AppProvider>` + router + MSW fetch boundary
 * (`renderWithProviders`). MSW is browser-lifecycle evidence only; persisted
 * commit claims live in the task-owned backend probe.
 */
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { http, HttpResponse, type HttpResponseResolver } from 'msw';
import { MemoryRouter, useNavigate } from 'react-router-dom';
import { describe, expect, test, vi } from 'vitest';
import { AppRoutes } from '@/routes';
import { AppProvider, makeQueryClient } from '@/design-system/providers/AppProvider';
import { I18nTestBoundary, renderWithProviders } from '@/test/render';
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

type CapturedPart = {
  field: string;
  /** Third FormData.set arg — the allocated artifact name on the wire. */
  filename: string;
  /** The actual File handed to the preparation seam. */
  originalName: string;
  size: number;
  /** The retained File object itself (selection identity). */
  file: File;
  bytes: Promise<number[]>;
};

/**
 * Observe the real preparation seam: `uploadArtifact` calls
 * `FormData.set('file', file, name)`. Capturing the File there is retention
 * evidence (actual name + size + bytes), unlike a filename in error text or a
 * literal fixture size.
 */
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
          file: value,
          bytes: new Promise((resolve) => {
            const reader = new FileReader();
            reader.onload = () =>
              resolve(Array.from(new Uint8Array(reader.result as ArrayBuffer)));
            reader.readAsArrayBuffer(value);
          }),
        });
      }
      return originalSet.call(this, field, value as Blob, filename);
    });
  return { parts, restore: () => spy.mockRestore() };
}

/** The chip whose remove button is in scope (never an error line). */
function chipFor(fileName: string): HTMLElement {
  const remove = screen.getByRole('button', { name: 'Remove attachment' });
  const chip = remove.closest('span');
  if (!chip) throw new Error('attachment chip not found');
  within(chip).getByText(fileName);
  return chip;
}

/** Freeze the artifact-name timestamp only (timers stay real). */
function freezeArtifactClock(iso = '2026-09-21T00:00:00.000Z') {
  return vi.spyOn(Date.prototype, 'toISOString').mockReturnValue(iso);
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
  test('a 413 upload rejection names the file, maps the code, sends nothing and retains the File (C2.4)', async () => {
    sessionStorage.setItem('happyranch.token', 'tok');
    stubBaseHandlers();
    stubThread('THR-001');
    const sends: { url: string; body: unknown }[] = [];
    let firstAttempts = 0;
    let retryAttempts = 0;
    let retried = false;
    const { parts, restore } = captureFormParts();
    server.use(
      http.post(`/api/v1/orgs/${SLUG}/artifacts`, () => {
        if (retried) retryAttempts += 1;
        else firstAttempts += 1;
        return HttpResponse.json(
          { detail: { code: 'artifact_too_large', max_bytes: 10, size_bytes: 11 } },
          { status: 413 },
        );
      }),
      http.post(`/api/v1/orgs/${SLUG}/threads/THR-001/send`, sendRecorder(sends)),
    );

    try {
      const user = userEvent.setup();
      renderWithProviders(<AppRoutes />, { route: `/orgs/${SLUG}/threads/THR-001` });
      const composer = await screen.findByLabelText(/Compose follow-up/i);
      await user.type(composer, 'keep this draft');
      await user.upload(
        await screen.findByLabelText(/Attach files/i),
        new File(['0123456789'], 'big.txt', { type: 'text/plain' }),
      );
      await user.click(screen.getByRole('button', { name: /^Send$/i }));
      const error = await screen.findByText(/too large to upload/i);
      expect(error.textContent).toContain('big.txt');
      expect(firstAttempts).toBe(1);
      expect(retryAttempts).toBe(0);
      expect(sends).toHaveLength(0);
      // The rejected request really carried the File at the preparation seam.
      expect(parts).toHaveLength(1);
      expect(parts[0].field).toBe('file');
      expect(parts[0].originalName).toBe('big.txt');
      expect(parts[0].size).toBe(10);
      // Chip retained with true File.name; draft nonempty; controls released.
      chipFor('big.txt');
      expect((composer as HTMLTextAreaElement).value).toBe('keep this draft');
      await waitFor(() =>
        expect(screen.getByRole('button', { name: /^Send$/i })).not.toBeDisabled(),
      );
      expect(screen.getByLabelText(/Attach files/i)).not.toBeDisabled();
      expect(screen.getByRole('button', { name: 'Remove attachment' })).not.toBeDisabled();

      // Manual retry WITHOUT reselecting: the retained File is observed at the
      // preparation seam under its unchanged reserved name.
      retried = true;
      await user.click(screen.getByRole('button', { name: /^Send$/i }));
      await waitFor(() => expect(retryAttempts).toBe(1));
      expect(firstAttempts).toBe(1);
      expect(sends).toHaveLength(0);
      expect(parts).toHaveLength(2);
      expect(parts[1].file).toBe(parts[0].file);
      expect(parts[1].originalName).toBe('big.txt');
      expect(parts[1].size).toBe(10);
      expect(parts[1].filename).toBe(parts[0].filename);
      expect(await parts[1].bytes).toEqual(await parts[0].bytes);
    } finally {
      restore();
    }
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
    const parts: CapturedPart[] = [];
    let firstAttempts = 0;
    let retryAttempts = 0;
    let retried = false;
    const setSpy = vi
      .spyOn(FormData.prototype, 'set')
      .mockImplementation(function (this: FormData, field: string, value: unknown, filename?: string) {
        if (value instanceof File) {
          parts.push({
            field,
            filename: filename ?? value.name,
            originalName: value.name,
            size: value.size,
            file: value,
            bytes: new Promise((resolve) => {
              const reader = new FileReader();
              reader.onload = () =>
                resolve(Array.from(new Uint8Array(reader.result as ArrayBuffer)));
              reader.readAsArrayBuffer(value);
            }),
          });
        }
        if (retried) retryAttempts += 1;
        else firstAttempts += 1;
        throw new Error('simulated preparation failure');
      });

    try {
      const user = userEvent.setup();
      renderWithProviders(<AppRoutes />, { route: `/orgs/${SLUG}/threads/THR-001` });
      const composer = await screen.findByLabelText(/Compose follow-up/i);
      await user.type(composer, 'prep draft');
      await user.upload(
        screen.getByLabelText(/Attach files/i),
        new File(['abc'], 'prep.txt', { type: 'text/plain' }),
      );
      await user.click(screen.getByRole('button', { name: /^Send$/i }));
      const error = await screen.findByText(/simulated preparation failure/i);
      expect(error.textContent).toContain('prep.txt');
      expect(artifactFetches).toBe(0);
      expect(sends).toHaveLength(0);
      expect(firstAttempts).toBe(1);
      expect(retryAttempts).toBe(0);
      // The File reached the invoked preparation seam with its true metadata.
      expect(parts).toHaveLength(1);
      expect(parts[0].originalName).toBe('prep.txt');
      expect(parts[0].size).toBe(3);
      chipFor('prep.txt');
      expect((composer as HTMLTextAreaElement).value).toBe('prep draft');
      await waitFor(() =>
        expect(screen.getByRole('button', { name: /^Send$/i })).not.toBeDisabled(),
      );
      expect(screen.getByLabelText(/Attach files/i)).not.toBeDisabled();
      expect(screen.getByRole('button', { name: 'Remove attachment' })).not.toBeDisabled();

      // Manual retry WITHOUT reselecting: the same retained File reaches the
      // preparation seam again (repeat failure) with unchanged metadata.
      retried = true;
      await user.click(screen.getByRole('button', { name: /^Send$/i }));
      await waitFor(() => expect(retryAttempts).toBe(1));
      expect(firstAttempts).toBe(1);
      expect(artifactFetches).toBe(0);
      expect(sends).toHaveLength(0);
      expect(parts).toHaveLength(2);
      expect(parts[1].file).toBe(parts[0].file);
      expect(parts[1].originalName).toBe('prep.txt');
      expect(parts[1].size).toBe(3);
      expect(parts[1].filename).toBe(parts[0].filename);
    } finally {
      setSpy.mockRestore();
    }
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

describe('attachment lifecycle — remaining failure seams and boundaries (TASK-8616)', () => {
  test('a transport-unknown upload makes exactly one attempt, retains the File and zero sends (C2.2)', async () => {
    sessionStorage.setItem('happyranch.token', 'tok');
    stubBaseHandlers();
    stubThread('THR-001');
    const sends: { url: string; body: unknown }[] = [];
    let firstAttempts = 0;
    let retryAttempts = 0;
    let retried = false;
    const { parts, restore } = captureFormParts();
    server.use(
      http.post(`/api/v1/orgs/${SLUG}/artifacts`, () => {
        if (retried) retryAttempts += 1;
        else firstAttempts += 1;
        return HttpResponse.error();
      }),
      http.post(`/api/v1/orgs/${SLUG}/threads/THR-001/send`, sendRecorder(sends)),
    );

    try {
      const user = userEvent.setup();
      renderWithProviders(<AppRoutes />, { route: `/orgs/${SLUG}/threads/THR-001` });
      const composer = await screen.findByLabelText(/Compose follow-up/i);
      await user.type(composer, 'unknown draft');
      await user.upload(
        screen.getByLabelText(/Attach files/i),
        new File(['abc'], 'note.txt', { type: 'text/plain' }),
      );
      await user.click(screen.getByRole('button', { name: /^Send$/i }));
      await screen.findByText(/Failed to fetch/i);
      expect(firstAttempts).toBe(1);
      expect(retryAttempts).toBe(0);
      expect(sends).toHaveLength(0);
      // Chip retains the actual File.name; the File is retained at the seam.
      chipFor('note.txt');
      expect(parts).toHaveLength(1);
      expect(parts[0].originalName).toBe('note.txt');
      expect(parts[0].size).toBe(3);
      expect((composer as HTMLTextAreaElement).value).toBe('unknown draft');
      await waitFor(() =>
        expect(screen.getByRole('button', { name: /^Send$/i })).not.toBeDisabled(),
      );
      expect(screen.getByLabelText(/Attach files/i)).not.toBeDisabled();
      expect(screen.getByRole('button', { name: 'Remove attachment' })).not.toBeDisabled();

      // Manual retry WITHOUT reselecting: the retained File is observed at the
      // preparation seam under its unchanged reserved name.
      retried = true;
      await user.click(screen.getByRole('button', { name: /^Send$/i }));
      await waitFor(() => expect(retryAttempts).toBe(1));
      expect(firstAttempts).toBe(1);
      expect(sends).toHaveLength(0);
      expect(parts).toHaveLength(2);
      expect(parts[1].file).toBe(parts[0].file);
      expect(parts[1].originalName).toBe('note.txt');
      expect(parts[1].size).toBe(3);
      expect(parts[1].filename).toBe(parts[0].filename);
      expect(await parts[1].bytes).toEqual(await parts[0].bytes);
    } finally {
      restore();
    }
  });

  test('a 400 invalid_artifact_name maps the code, retains the File and sends nothing (C2.3)', async () => {
    sessionStorage.setItem('happyranch.token', 'tok');
    stubBaseHandlers();
    stubThread('THR-001');
    const sends: { url: string; body: unknown }[] = [];
    let firstAttempts = 0;
    let retryAttempts = 0;
    let retried = false;
    const { parts, restore } = captureFormParts();
    server.use(
      http.post(`/api/v1/orgs/${SLUG}/artifacts`, () => {
        if (retried) retryAttempts += 1;
        else firstAttempts += 1;
        return HttpResponse.json(
          { detail: { code: 'invalid_artifact_name' } },
          { status: 400 },
        );
      }),
      http.post(`/api/v1/orgs/${SLUG}/threads/THR-001/send`, sendRecorder(sends)),
    );

    try {
      const user = userEvent.setup();
      renderWithProviders(<AppRoutes />, { route: `/orgs/${SLUG}/threads/THR-001` });
      const composer = await screen.findByLabelText(/Compose follow-up/i);
      await user.type(composer, 'bad name draft');
      await user.upload(
        screen.getByLabelText(/Attach files/i),
        new File(['abc'], 'weird?.txt', { type: 'text/plain' }),
      );
      await user.click(screen.getByRole('button', { name: /^Send$/i }));
      const error = await screen.findByText(/name is not allowed/i);
      expect(error.textContent).toContain('weird?.txt');
      expect(firstAttempts).toBe(1);
      expect(retryAttempts).toBe(0);
      expect(sends).toHaveLength(0);
      chipFor('weird?.txt');
      expect(parts).toHaveLength(1);
      expect(parts[0].originalName).toBe('weird?.txt');
      expect(parts[0].size).toBe(3);
      expect((composer as HTMLTextAreaElement).value).toBe('bad name draft');
      await waitFor(() =>
        expect(screen.getByRole('button', { name: /^Send$/i })).not.toBeDisabled(),
      );
      expect(screen.getByLabelText(/Attach files/i)).not.toBeDisabled();
      expect(screen.getByRole('button', { name: 'Remove attachment' })).not.toBeDisabled();

      // Manual retry WITHOUT reselecting: the retained File is observed at the
      // preparation seam under its unchanged reserved name.
      retried = true;
      await user.click(screen.getByRole('button', { name: /^Send$/i }));
      await waitFor(() => expect(retryAttempts).toBe(1));
      expect(firstAttempts).toBe(1);
      expect(sends).toHaveLength(0);
      expect(parts).toHaveLength(2);
      expect(parts[1].file).toBe(parts[0].file);
      expect(parts[1].originalName).toBe('weird?.txt');
      expect(parts[1].size).toBe(3);
      expect(parts[1].filename).toBe(parts[0].filename);
      expect(await parts[1].bytes).toEqual(await parts[0].bytes);
    } finally {
      restore();
    }
  });

  test('equal-metadata files with different bytes reserve distinct names and never conflate bytes (C3.3 equal-metadata)', async () => {
    sessionStorage.setItem('happyranch.token', 'tok');
    stubBaseHandlers();
    stubThread('THR-001');
    const sends: { url: string; body: unknown }[] = [];
    const uploadNames: string[] = [];
    // Capture the actual multipart parts at the FormData preparation seam: the
    // part field name, the File (name/size) and its bytes are exactly what
    // uploadArtifact sets. The backend probe independently proves persisted bytes.
    const { parts, restore } = captureFormParts();
    // Freeze the artifact-name timestamp so the same-basename collision is
    // deterministic rather than depending on the wall clock crossing a second.
    const clock = freezeArtifactClock();
    let attempt = 0;
    server.use(
      http.post(`/api/v1/orgs/${SLUG}/artifacts`, async ({ request }) => {
        const name = requestedName(request);
        uploadNames.push(name);
        attempt += 1;
        // Deterministically fail the second selection's first attempt.
        if (attempt === 2) return HttpResponse.error();
        return HttpResponse.json({ name, size_bytes: 3, modified_at: 'now' });
      }),
      http.post(`/api/v1/orgs/${SLUG}/threads/THR-001/send`, sendRecorder(sends)),
    );

    try {
      const user = userEvent.setup();
      renderWithProviders(<AppRoutes />, { route: `/orgs/${SLUG}/threads/THR-001` });
      // Identical name/size/lastModified, different bytes.
      await user.upload(await screen.findByLabelText(/Attach files/i), [
        new File(['AAA'], 'same.txt', { type: 'text/plain', lastModified: 7 }),
        new File(['BBB'], 'same.txt', { type: 'text/plain', lastModified: 7 }),
      ]);
      await user.click(screen.getByRole('button', { name: /^Send$/i }));
      await screen.findByText(/Failed to fetch/i);
      // Both selections kept distinct chips.
      expect(screen.getAllByRole('button', { name: 'Remove attachment' })).toHaveLength(2);
      await user.click(screen.getByRole('button', { name: /^Send$/i }));
      await waitFor(() => expect(sends).toHaveLength(1));

      expect(uploadNames).toHaveLength(3);
      const aName = uploadNames[0];
      const bName = uploadNames[1];
      expect(aName).not.toBe(bName);
      // The failed selection is retried under its retained (same) name.
      expect(uploadNames[2]).toBe(bName);
      // Actual multipart parts: three parts, part name `file`, exact File name
      // and size retained, and the retried file carries B's bytes (not A's).
      expect(parts.map((p) => p.field)).toEqual(['file', 'file', 'file']);
      expect(parts.map((p) => p.filename)).toEqual([aName, bName, bName]);
      expect(parts.map((p) => p.originalName)).toEqual(['same.txt', 'same.txt', 'same.txt']);
      expect(parts.map((p) => p.size)).toEqual([3, 3, 3]);
      expect(await parts[0].bytes).toEqual([65, 65, 65]);
      expect(await parts[2].bytes).toEqual([66, 66, 66]);
      const refs = attachmentsOf(sends[0].body);
      expect(refs.map((r) => r.artifact_name)).toEqual([aName, bName]);
      expect(refs.map((r) => r.display_name)).toEqual(['same.txt', 'same.txt']);
    } finally {
      restore();
      clock.mockRestore();
    }
  });

  test('sanitized-equal names collide but still reserve distinct artifacts deterministically (C3.3 sanitized-equal)', async () => {
    sessionStorage.setItem('happyranch.token', 'tok');
    stubBaseHandlers();
    stubThread('THR-001');
    const sends: { url: string; body: unknown }[] = [];
    const uploadNames: string[] = [];
    const { parts, restore } = captureFormParts();
    const clock = freezeArtifactClock();
    let attempt = 0;
    server.use(
      http.post(`/api/v1/orgs/${SLUG}/artifacts`, async ({ request }) => {
        const name = requestedName(request);
        uploadNames.push(name);
        attempt += 1;
        if (attempt === 2) return HttpResponse.error();
        return HttpResponse.json({ name, size_bytes: 3, modified_at: 'now' });
      }),
      http.post(`/api/v1/orgs/${SLUG}/threads/THR-001/send`, sendRecorder(sends)),
    );

    try {
      const user = userEvent.setup();
      renderWithProviders(<AppRoutes />, { route: `/orgs/${SLUG}/threads/THR-001` });
      // `a b.txt` and `a-b.txt` both sanitize to `a-b.txt`; frozen clock makes
      // the sanitized bases collide on the same second.
      await user.upload(await screen.findByLabelText(/Attach files/i), [
        new File(['AAA'], 'a b.txt', { type: 'text/plain' }),
        new File(['BBB'], 'a-b.txt', { type: 'text/plain' }),
      ]);
      await user.click(screen.getByRole('button', { name: /^Send$/i }));
      await screen.findByText(/Failed to fetch/i);
      await user.click(screen.getByRole('button', { name: /^Send$/i }));
      await waitFor(() => expect(sends).toHaveLength(1));

      expect(uploadNames).toHaveLength(3);
      const aName = uploadNames[0];
      const bName = uploadNames[1];
      expect(aName).not.toBe(bName);
      // The first selection keeps index 1; the colliding second takes index 2.
      expect(aName).toMatch(/-a-b\.txt$/);
      expect(bName).toMatch(/-2-a-b\.txt$/);
      expect(uploadNames[2]).toBe(bName);
      expect(await parts[0].bytes).toEqual([65, 65, 65]);
      expect(await parts[2].bytes).toEqual([66, 66, 66]);
      const refs = attachmentsOf(sends[0].body);
      expect(refs.map((r) => r.artifact_name)).toEqual([aName, bName]);
    } finally {
      restore();
      clock.mockRestore();
    }
  });

  test('removing a completed selection sends neither it nor a re-upload (C3.4b)', async () => {
    sessionStorage.setItem('happyranch.token', 'tok');
    stubBaseHandlers();
    stubThread('THR-001');
    const sends: { url: string; body: unknown }[] = [];
    const uploadedNames: string[] = [];
    let sendAttempts = 0;
    server.use(
      http.post(`/api/v1/orgs/${SLUG}/artifacts`, async ({ request }) => {
        const name = requestedName(request);
        uploadedNames.push(name);
        return HttpResponse.json({ name, size_bytes: 3, modified_at: 'now' });
      }),
      http.post(`/api/v1/orgs/${SLUG}/threads/THR-001/send`, async ({ request }) => {
        sendAttempts += 1;
        sends.push({ url: new URL(request.url).pathname, body: await request.json() });
        if (sendAttempts === 1) {
          return HttpResponse.json({ detail: { code: 'internal' } }, { status: 500 });
        }
        return HttpResponse.json({ thread_id: 'x', seq: 2 });
      }),
    );

    const user = userEvent.setup();
    renderWithProviders(<AppRoutes />, { route: `/orgs/${SLUG}/threads/THR-001` });
    await user.upload(await screen.findByLabelText(/Attach files/i), [
      new File(['aaa'], 'a.txt', { type: 'text/plain' }),
      new File(['bbb'], 'b.txt', { type: 'text/plain' }),
    ]);
    // First send fails after both uploads, so both refs are retained.
    await user.click(screen.getByRole('button', { name: /^Send$/i }));
    await waitFor(() => expect(sends).toHaveLength(1));
    expect(uploadedNames).toHaveLength(2);

    // Remove the completed selection A, then add replacement C.
    await user.click(screen.getAllByRole('button', { name: 'Remove attachment' })[0]);
    await user.upload(
      screen.getByLabelText(/Attach files/i),
      new File(['ccc'], 'c.txt', { type: 'text/plain' }),
    );
    await user.click(screen.getByRole('button', { name: /^Send$/i }));
    await waitFor(() => expect(sends).toHaveLength(2));

    // Only B reused, only C newly uploaded; A is neither re-uploaded nor sent.
    expect(uploadedNames).toHaveLength(3);
    const refs = attachmentsOf(sends[1].body);
    expect(refs.map((r) => r.display_name)).toEqual(['b.txt', 'c.txt']);
    expect(refs[0].artifact_name).toBe(uploadedNames[1]);
    expect(refs.some((r) => r.artifact_name === uploadedNames[0])).toBe(false);
  });
});

describe('attachment lifecycle — known rejection and missing-ref recovery (C4)', () => {
  test('a precommit 400 thread_not_open retains exact refs, uploads nothing on retry, and re-sends them (C4.1)', async () => {
    sessionStorage.setItem('happyranch.token', 'tok');
    stubBaseHandlers();
    stubThread('THR-001');
    const sends: { url: string; body: unknown }[] = [];
    const uploadNames: string[] = [];
    let sendAttempt = 0;
    server.use(
      http.post(`/api/v1/orgs/${SLUG}/artifacts`, async ({ request }) => {
        const name = requestedName(request);
        uploadNames.push(name);
        return HttpResponse.json({ name, size_bytes: 3, modified_at: 'now' });
      }),
      http.post(`/api/v1/orgs/${SLUG}/threads/THR-001/send`, async ({ request }) => {
        sendAttempt += 1;
        sends.push({ url: new URL(request.url).pathname, body: await request.json() });
        if (sendAttempt === 1) {
          return HttpResponse.json({ detail: { code: 'thread_not_open' } }, { status: 400 });
        }
        return HttpResponse.json({ thread_id: 'THR-001', seq: 2 });
      }),
    );

    const user = userEvent.setup();
    renderWithProviders(<AppRoutes />, { route: `/orgs/${SLUG}/threads/THR-001` });
    const composer = await screen.findByLabelText(/Compose follow-up/i);
    await user.type(composer, 'thread closed retry');
    await user.upload(
      screen.getByLabelText(/Attach files/i),
      new File(['aaa'], 'a.txt', { type: 'text/plain' }),
    );
    await user.click(screen.getByRole('button', { name: /^Send$/i }));
    const error = await screen.findByText('This thread is no longer open.');
    // A send rejection must not be labelled with the last uploaded file.
    expect(error.textContent?.startsWith('a.txt:')).toBe(false);
    expect(sends).toHaveLength(1);
    expect(uploadNames).toHaveLength(1);
    expect((composer as HTMLTextAreaElement).value).toBe('thread closed retry');
    expect(screen.getAllByText(/a\.txt/).length).toBeGreaterThanOrEqual(1);
    expect(screen.getByLabelText(/Attach files/i)).not.toBeDisabled();
    await waitFor(() =>
      expect(screen.getByRole('button', { name: /^Send$/i })).not.toBeDisabled(),
    );

    // Explicit manual retry: zero new uploads, the exact same retained ref.
    await user.click(screen.getByRole('button', { name: /^Send$/i }));
    await waitFor(() => expect(sends).toHaveLength(2));
    expect(uploadNames).toHaveLength(1);
    const firstRefs = (sends[0].body as { attachments: { artifact_name: string }[] }).attachments;
    const retryRefs = (sends[1].body as { attachments: { artifact_name: string }[] }).attachments;
    expect(retryRefs.map((r) => r.artifact_name)).toEqual(firstRefs.map((r) => r.artifact_name));
    await waitFor(() =>
      expect((screen.getByLabelText(/Compose follow-up/i) as HTMLTextAreaElement).value).toBe(''),
    );
    expect(screen.queryByText('a.txt')).toBeNull();
  });

  test('a 404 artifact_not_found send persists until remove/reselect/reupload and keeps the other ref exact (C4.2)', async () => {
    sessionStorage.setItem('happyranch.token', 'tok');
    stubBaseHandlers();
    stubThread('THR-001');
    const sends: { url: string; body: unknown }[] = [];
    const uploadNames: string[] = [];
    let sendAttempt = 0;
    server.use(
      http.post(`/api/v1/orgs/${SLUG}/artifacts`, async ({ request }) => {
        const name = requestedName(request);
        uploadNames.push(name);
        return HttpResponse.json({ name, size_bytes: 3, modified_at: 'now' });
      }),
      http.post(`/api/v1/orgs/${SLUG}/threads/THR-001/send`, async ({ request }) => {
        sendAttempt += 1;
        sends.push({ url: new URL(request.url).pathname, body: await request.json() });
        if (sendAttempt <= 2) {
          return HttpResponse.json({ detail: { code: 'artifact_not_found' } }, { status: 404 });
        }
        return HttpResponse.json({ thread_id: 'THR-001', seq: 2 });
      }),
    );

    const user = userEvent.setup();
    renderWithProviders(<AppRoutes />, { route: `/orgs/${SLUG}/threads/THR-001` });
    const composer = await screen.findByLabelText(/Compose follow-up/i);
    await user.type(composer, 'missing ref retry');
    await user.upload(screen.getByLabelText(/Attach files/i), [
      new File(['aaa'], 'a.txt', { type: 'text/plain' }),
      new File(['bbb'], 'b.txt', { type: 'text/plain' }),
    ]);
    await user.click(screen.getByRole('button', { name: /^Send$/i }));
    const error = await screen.findByText(
      'That attachment is no longer available; remove it and attach it again.',
    );
    expect(error.textContent?.startsWith('b.txt:')).toBe(false);
    expect(sends).toHaveLength(1);
    expect(uploadNames).toHaveLength(2);
    expect((composer as HTMLTextAreaElement).value).toBe('missing ref retry');
    expect(screen.getAllByRole('button', { name: 'Remove attachment' })).toHaveLength(2);

    // Retry unchanged: still rejected, still zero new uploads.
    await user.click(screen.getByRole('button', { name: /^Send$/i }));
    await waitFor(() => expect(sends).toHaveLength(2));
    expect(uploadNames).toHaveLength(2);

    // Remove B, reselect/reupload it, then the retry succeeds with A's ref exact.
    await user.click(screen.getAllByRole('button', { name: 'Remove attachment' })[1]);
    await user.upload(
      screen.getByLabelText(/Attach files/i),
      new File(['bbb2'], 'b.txt', { type: 'text/plain' }),
    );
    await user.click(screen.getByRole('button', { name: /^Send$/i }));
    await waitFor(() => expect(sends).toHaveLength(3));
    expect(uploadNames).toHaveLength(3);
    const firstRefs = (sends[0].body as { attachments: { artifact_name: string }[] }).attachments;
    const finalRefs = (sends[2].body as { attachments: { artifact_name: string }[] }).attachments;
    expect(finalRefs.map((r) => r.artifact_name)).toEqual([uploadNames[0], uploadNames[2]]);
    expect(finalRefs[0].artifact_name).toBe(firstRefs[0].artifact_name);
  });
});

describe('attachment lifecycle — lost response and post-200 tail failure (C6)', () => {
  test('a lost send response is one attempt, keeps the draft/chip and never auto-resends (C6.1)', async () => {
    sessionStorage.setItem('happyranch.token', 'tok');
    stubBaseHandlers();
    stubThread('THR-001');
    let sendCount = 0;
    server.use(
      http.post(`/api/v1/orgs/${SLUG}/artifacts`, async ({ request }) =>
        HttpResponse.json({ name: requestedName(request), size_bytes: 3, modified_at: 'now' }),
      ),
      http.post(`/api/v1/orgs/${SLUG}/threads/THR-001/send`, () => {
        sendCount += 1;
        // No response: outcome unknown. A mock counter is not commit proof.
        return HttpResponse.error();
      }),
    );

    const user = userEvent.setup();
    renderWithProviders(<AppRoutes />, { route: `/orgs/${SLUG}/threads/THR-001` });
    const composer = await screen.findByLabelText(/Compose follow-up/i);
    await user.type(composer, 'lost');
    await user.upload(
      screen.getByLabelText(/Attach files/i),
      new File(['abc'], 'note.txt', { type: 'text/plain' }),
    );
    await user.click(screen.getByRole('button', { name: /^Send$/i }));
    await screen.findByText(/Failed to fetch/i);
    expect(sendCount).toBe(1);
    // Draft and chip retained; no automatic resend.
    expect((screen.getByLabelText(/Compose follow-up/i) as HTMLTextAreaElement).value).toBe('lost');
    expect(screen.getAllByText(/note\.txt/).length).toBeGreaterThanOrEqual(1);
    await new Promise((resolve) => setTimeout(resolve, 500));
    expect(sendCount).toBe(1);
  });

  test('a 200 clears the draft/refs and a failed retrieval then recovers the acknowledged message exactly once (C6.2)', async () => {
    sessionStorage.setItem('happyranch.token', 'tok');
    stubBaseHandlers();
    stubThread('THR-001');
    let sendCount = 0;
    let committed = false;
    let messagesFailureConsumed = false;
    const ackMessage = {
      seq: 2,
      speaker: 'founder',
      kind: 'message',
      body_markdown: 'sent',
      decline_reason: null,
      system_payload: null,
      attachments: [],
      created_at: 'now',
      responder_status: [],
    };
    const canonicalResponses: { body_markdown: string | null }[][] = [];
    server.use(
      http.post(`/api/v1/orgs/${SLUG}/artifacts`, async ({ request }) =>
        HttpResponse.json({ name: requestedName(request), size_bytes: 3, modified_at: 'now' }),
      ),
      http.post(`/api/v1/orgs/${SLUG}/threads/THR-001/send`, () => {
        sendCount += 1;
        committed = true;
        return HttpResponse.json({ thread_id: 'x', seq: 2 });
      }),
      http.get(`/api/v1/orgs/${SLUG}/threads/THR-001/messages`, () => {
        // The post-acknowledgment retrieval fails exactly once; later
        // canonical retrievals return the acknowledged message.
        if (committed && !messagesFailureConsumed) {
          messagesFailureConsumed = true;
          return HttpResponse.error();
        }
        const messages = committed ? [ackMessage] : [];
        canonicalResponses.push(messages);
        return HttpResponse.json({
          messages,
          has_more: false,
          next_since_seq: ackMessage.seq,
          reply_delivery: [],
        });
      }),
    );

    const client = makeQueryClient();
    const user = userEvent.setup();
    render(
      <MemoryRouter initialEntries={[`/orgs/${SLUG}/threads/THR-001`]}>
        {/* Bespoke mount mirrors production `AppShell` (I18nProvider outside
            AppProvider): the W2a shell reads locale from the real provider. */}
        <I18nTestBoundary>
          <AppProvider client={client}>
            <AppRoutes />
          </AppProvider>
        </I18nTestBoundary>
      </MemoryRouter>,
    );
    const composer = await screen.findByLabelText(/Compose follow-up/i);
    await user.type(composer, 'sent');
    await user.upload(
      screen.getByLabelText(/Attach files/i),
      new File(['abc'], 'note.txt', { type: 'text/plain' }),
    );
    await user.click(screen.getByRole('button', { name: /^Send$/i }));
    // The failing post-acknowledgment GET is actually consumed.
    await waitFor(() => expect(messagesFailureConsumed).toBe(true));
    await waitFor(() => expect(sendCount).toBe(1));
    // The 200 clears the originating draft and chips.
    await waitFor(() =>
      expect((screen.getByLabelText(/Compose follow-up/i) as HTMLTextAreaElement).value).toBe(''),
    );
    expect(screen.queryByText('note.txt')).toBeNull();

    // A canonical retrieval now recovers the acknowledged message exactly once.
    await client.invalidateQueries({ queryKey: ['thread-messages', SLUG, 'THR-001'] });
    await waitFor(() => expect(screen.getAllByText('sent')).toHaveLength(1));
    const lastCanonical = canonicalResponses.at(-1)!;
    expect(lastCanonical).toHaveLength(1);
    expect(lastCanonical[0].body_markdown).toBe('sent');

    // No draft/ref resurrection and no resend after recovery.
    await new Promise((resolve) => setTimeout(resolve, 300));
    expect(sendCount).toBe(1);
    expect((screen.getByLabelText(/Compose follow-up/i) as HTMLTextAreaElement).value).toBe('');
    expect(screen.queryByText('note.txt')).toBeNull();
  });
});
