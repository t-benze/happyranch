/**
 * TASK-8599 — NewThreadDialog attachment cases (accepted case-design C1.3, C2
 * new-thread variant, C8.3). Real `<AppProvider>` + router + MSW boundary.
 */
import { act, fireEvent, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { http, HttpResponse } from 'msw';
import { useState } from 'react';
import { Route, Routes, useLocation, useNavigate } from 'react-router-dom';
import { beforeEach, describe, expect, test, vi } from 'vitest';
import { AppRoutes } from '@/routes';
import { OrgProvider } from '@/lib/orgSlug';
import { renderWithProviders } from '@/test/render';
import { server } from '@/test/server';
import { NewThreadDialog } from './NewThreadDialog';

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

type CapturedPart = {
  field: string;
  filename: string;
  originalName: string;
  size: number;
  /** The actual retained File object handed to the preparation seam. */
  file: File;
  bytes: Promise<number[]>;
};

/** Observe the real FormData preparation seam (actual File name + size + bytes). */
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
      expect(firstAttempts).toBe(1);
      expect(retryAttempts).toBe(0);
      expect(composeCount).toBe(0);
      expect(screen.getByRole('dialog')).toBeInTheDocument();
      // Chip-scoped retained File.name (never an error-line regex).
      chipFor('big.txt');
      expect(parts).toHaveLength(1);
      expect(parts[0].originalName).toBe('big.txt');
      expect(parts[0].size).toBe(10);
      expect(screen.getByLabelText(/^Subject$/i)).toHaveValue('Hi');
      expect(screen.getByLabelText(/Attach files/i)).not.toBeDisabled();
      expect(screen.getByRole('button', { name: 'Remove attachment' })).not.toBeDisabled();
      await waitFor(() =>
        expect(screen.getByRole('button', { name: /^Send$/i })).not.toBeDisabled(),
      );

      // Manual retry WITHOUT reselecting: the retained File reaches the
      // preparation seam again under its unchanged reserved name.
      retried = true;
      await user.click(screen.getByRole('button', { name: /^Send$/i }));
      await waitFor(() => expect(retryAttempts).toBe(1));
      expect(firstAttempts).toBe(1);
      expect(composeCount).toBe(0);
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

  // Both origin and replacement actually submit; old POST success/rejection x
  // old-before-new / new-before-old for a closed and reopened dialog. Each
  // settle waits for the mocked response delivery (`response:mocked`) and then
  // flushes the async terminal handling before asserting that the reopened
  // dialog's own held submission is still pending.
  for (const oldReject of [false, true]) {
    for (const oldFirst of [false, true]) {
      test(`a closed/reopened dialog is not closed or unlocked by the abandoned submission (C8.3 oldReject=${oldReject}, oldFirst=${oldFirst})`, async () => {
        sessionStorage.setItem('happyranch.token', 'tok');
        stubBaseHandlers();
        stubCreatedThread('THR-NEW');
        server.use(
          http.get(`/api/v1/orgs/${SLUG}/dashboard/summary`, () =>
            HttpResponse.json({ threads: {}, tasks: {} }),
          ),
        );
        let releaseOld: () => void = () => {};
        let releaseNew: () => void = () => {};
        const oldHeld = new Promise<void>((resolve) => { releaseOld = resolve; });
        const newHeld = new Promise<void>((resolve) => { releaseNew = resolve; });
        const bodies: {
          subject?: string;
          body_markdown?: string;
          attachments?: { display_name: string }[];
        }[] = [];
        const uploadNames: string[] = [];
        let responses = 0;
        const observer = ({ request }: { request: Request }) => {
          if (
            request.method === 'POST' &&
            new URL(request.url).pathname === `/api/v1/orgs/${SLUG}/threads`
          ) {
            responses += 1;
          }
        };
        server.events.on('response:mocked', observer);
        server.use(
          http.post(`/api/v1/orgs/${SLUG}/artifacts`, ({ request }) => {
            const name = requestedName(request);
            uploadNames.push(name);
            return HttpResponse.json({ name, size_bytes: 3, modified_at: 'now' });
          }),
          http.post(`/api/v1/orgs/${SLUG}/threads`, async ({ request }) => {
            const body = (await request.json()) as {
              subject?: string;
              body_markdown?: string;
              attachments?: { display_name: string }[];
            };
            bodies.push(body);
            if (bodies.length === 1) {
              await oldHeld;
              return oldReject
                ? HttpResponse.json(
                    { detail: { code: 'invalid_artifact_name' } },
                    { status: 400 },
                  )
                : HttpResponse.json(
                    { thread_id: 'THR-NEW', started_at: 'now', pending_replies: 1 },
                    { status: 201 },
                  );
            }
            await newHeld;
            return HttpResponse.json(
              { detail: { code: 'artifact_not_found' } },
              { status: 404 },
            );
          }),
        );
        const user = userEvent.setup();
        const settle = async (which: 'old' | 'new') => {
          const before = responses;
          if (which === 'old') releaseOld();
          else releaseNew();
          await waitFor(() => expect(responses).toBe(before + 1));
          await act(async () => { await new Promise((resolve) => setTimeout(resolve, 60)); });
        };
        try {
          renderWithProviders(
            <>
              <AppRoutes />
              <NavTo to={`/orgs/${SLUG}/threads`} label="probe" />
              <LocationProbe />
            </>,
            { route: `/orgs/${SLUG}/threads` },
          );
          await openDialog(user);
          await fillDialog(user, 'First', 'old body');
          await user.upload(
            screen.getByLabelText(/Attach files/i),
            new File(['AAA'], 'a.txt', { type: 'text/plain' }),
          );
          await user.click(screen.getByRole('button', { name: /^Send$/i }));
          await waitFor(() => expect(bodies).toHaveLength(1));
          // Close, then reopen and start a genuinely NEW submission.
          await user.click(screen.getByRole('button', { name: /^Cancel$/i }));
          await openDialog(user);
          await fillDialog(user, 'Second', 'new body');
          await user.upload(
            screen.getByLabelText(/Attach files/i),
            new File(['BBB'], 'b.txt', { type: 'text/plain' }),
          );
          await user.click(screen.getByRole('button', { name: /^Send$/i }));
          await waitFor(() => expect(bodies).toHaveLength(2));

          if (oldFirst) {
            await settle('old');
            // The old terminal handling ran; the reopened dialog's own held
            // submission still owns the pending state and its fields/chip.
            expect(screen.getByLabelText(/^Body \(Markdown\)$/i)).toBeDisabled();
            expect(screen.getByLabelText(/Attach files/i)).toBeDisabled();
            expect(screen.getByRole('button', { name: 'Remove attachment' })).toBeDisabled();
            expect(screen.getByRole('button', { name: /^Sending…$/i })).toBeDisabled();
            expect(screen.getByText('b.txt')).toBeInTheDocument();
            fireEvent.keyDown(screen.getByLabelText(/^Body \(Markdown\)$/i), { key: 'Enter' });
            expect(bodies).toHaveLength(2);
            await settle('new');
          } else {
            await settle('new');
            await settle('old');
          }

          expect(screen.getByRole('dialog')).toBeInTheDocument();
          expect(screen.getByLabelText(/^Subject$/i)).toHaveValue('Second');
          expect(screen.getByLabelText(/^Body \(Markdown\)$/i)).toHaveValue('new body');
          expect(screen.getByText('b.txt')).toBeInTheDocument();
          expect(
            screen.getByText(
              'That attachment is no longer available; remove it and attach it again.',
            ),
          ).toBeInTheDocument();
          expect(screen.getByTestId('review-location').textContent).toBe(`/orgs/${SLUG}/threads`);
          expect(
            bodies.map((b) => [
              b.subject,
              b.body_markdown,
              b.attachments?.map((a) => a.display_name),
            ]),
          ).toEqual([
            ['First', 'old body', ['a.txt']],
            ['Second', 'new body', ['b.txt']],
          ]);

          // Manual retry reuses the retained ref with no extra upload.
          await user.click(screen.getByRole('button', { name: /^Send$/i }));
          await waitFor(() => expect(bodies).toHaveLength(3));
          expect(bodies[2]).toEqual(bodies[1]);
          expect(uploadNames).toHaveLength(2);
        } finally {
          releaseOld();
          releaseNew();
          server.events.removeListener('response:mocked', observer);
        }
      });
    }
  }
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
      expect(firstAttempts).toBe(1);
      expect(retryAttempts).toBe(0);
      // Chip-scoped retained File.name (never an error-line regex).
      chipFor('prep.txt');
      expect(parts).toHaveLength(1);
      expect(parts[0].originalName).toBe('prep.txt');
      expect(parts[0].size).toBe(3);
      expect(screen.getByLabelText(/^Subject$/i)).toHaveValue('Hi');
      // Controls are released for a retry.
      await waitFor(() =>
        expect(screen.getByRole('button', { name: /^Send$/i })).not.toBeDisabled(),
      );
      expect(screen.getByLabelText(/Attach files/i)).not.toBeDisabled();
      expect(screen.getByRole('button', { name: 'Remove attachment' })).not.toBeDisabled();

      // Manual retry WITHOUT reselecting: the retained File reaches the
      // preparation seam again (repeat failure) with unchanged metadata.
      retried = true;
      await user.click(screen.getByRole('button', { name: /^Send$/i }));
      await waitFor(() => expect(retryAttempts).toBe(1));
      expect(firstAttempts).toBe(1);
      expect(artifactFetches).toBe(0);
      expect(composeCount).toBe(0);
      expect(parts).toHaveLength(2);
      expect(parts[1].file).toBe(parts[0].file);
      expect(parts[1].originalName).toBe('prep.txt');
      expect(parts[1].size).toBe(3);
      expect(parts[1].filename).toBe(parts[0].filename);
    } finally {
      setSpy.mockRestore();
    }
  });

  test('a transport-unknown upload attempts once, never composes and retains the File (C2.2 new-thread)', async () => {
    sessionStorage.setItem('happyranch.token', 'tok');
    stubBaseHandlers();
    let firstAttempts = 0;
    let retryAttempts = 0;
    let retried = false;
    let composeCount = 0;
    const { parts, restore } = captureFormParts();
    server.use(
      http.post(`/api/v1/orgs/${SLUG}/artifacts`, () => {
        if (retried) retryAttempts += 1;
        else firstAttempts += 1;
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
      expect(firstAttempts).toBe(1);
      expect(retryAttempts).toBe(0);
      expect(composeCount).toBe(0);
      // Chip-scoped retained File.name (never an error-line regex).
      chipFor('note.txt');
      expect(parts).toHaveLength(1);
      expect(parts[0].originalName).toBe('note.txt');
      expect(parts[0].size).toBe(3);
      expect(screen.getByLabelText(/^Subject$/i)).toHaveValue('Hi');
      await waitFor(() =>
        expect(screen.getByRole('button', { name: /^Send$/i })).not.toBeDisabled(),
      );
      expect(screen.getByLabelText(/Attach files/i)).not.toBeDisabled();
      expect(screen.getByRole('button', { name: 'Remove attachment' })).not.toBeDisabled();

      // Manual retry WITHOUT reselecting: the same retained File is observed at
      // the preparation seam under its unchanged reserved name.
      retried = true;
      await user.click(screen.getByRole('button', { name: /^Send$/i }));
      await waitFor(() => expect(retryAttempts).toBe(1));
      expect(firstAttempts).toBe(1);
      expect(composeCount).toBe(0);
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

  test('a 400 upload rejection maps the code, keeps the dialog open and retains the File (C2.3 new-thread)', async () => {
    sessionStorage.setItem('happyranch.token', 'tok');
    stubBaseHandlers();
    let composeCount = 0;
    let firstAttempts = 0;
    let retryAttempts = 0;
    let retried = false;
    const { parts, restore } = captureFormParts();
    server.use(
      http.post(`/api/v1/orgs/${SLUG}/artifacts`, () => {
        if (retried) retryAttempts += 1;
        else firstAttempts += 1;
        return HttpResponse.json({ detail: { code: 'invalid_artifact_name' } }, { status: 400 });
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
        new File(['abc'], 'weird?.txt', { type: 'text/plain' }),
      );
      await user.click(screen.getByRole('button', { name: /^Send$/i }));
      const error = await screen.findByText(/name is not allowed/i);
      expect(error.textContent).toContain('weird?.txt');
      expect(firstAttempts).toBe(1);
      expect(retryAttempts).toBe(0);
      expect(composeCount).toBe(0);
      expect(screen.getByRole('dialog')).toBeInTheDocument();
      // Chip-scoped retained File.name (never an error-line regex).
      chipFor('weird?.txt');
      expect(parts).toHaveLength(1);
      expect(parts[0].originalName).toBe('weird?.txt');
      expect(parts[0].size).toBe(3);
      expect(screen.getByLabelText(/^Subject$/i)).toHaveValue('Hi');
      await waitFor(() =>
        expect(screen.getByRole('button', { name: /^Send$/i })).not.toBeDisabled(),
      );
      expect(screen.getByLabelText(/Attach files/i)).not.toBeDisabled();
      expect(screen.getByRole('button', { name: 'Remove attachment' })).not.toBeDisabled();

      // Manual retry WITHOUT reselecting: the same retained File is observed at
      // the preparation seam under its unchanged reserved name.
      retried = true;
      await user.click(screen.getByRole('button', { name: /^Send$/i }));
      await waitFor(() => expect(retryAttempts).toBe(1));
      expect(firstAttempts).toBe(1);
      expect(composeCount).toBe(0);
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
    renderWithProviders(
      <>
        <AppRoutes />
        <LocationProbe />
      </>,
      { route: `/orgs/${SLUG}/threads` },
    );
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
    await act(async () => { await new Promise((resolve) => setTimeout(resolve, 60)); });
    expect(screen.queryByLabelText(/^Subject$/i)).toBeNull();
    // Final callback/location behavior after the old terminal handling: the
    // departed dialog's 201 must not call onCreated -> navigate to the thread.
    expect(screen.getByTestId('review-location').textContent).toBe(`/orgs/${SLUG}/threads`);
  });

  for (const oldReject of [false, true]) {
    test(`an org switch during upload keeps the compose at its captured org (C8.2b oldReject=${oldReject})`, async () => {
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
          return oldReject
            ? HttpResponse.json({ detail: { code: 'invalid_artifact_name' } }, { status: 400 })
            : HttpResponse.json(
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
      await act(async () => { await new Promise((resolve) => setTimeout(resolve, 60)); });
      // The compose URL stays at the org captured at first submit.
      expect(composeUrls[0]).toBe(`/api/v1/orgs/${SLUG}/threads`);
      // Org departure invalidates result ownership: neither the late alpha
      // success nor the late alpha rejection may navigate, error, or reset the
      // replacement beta view.
      expect(screen.getByTestId('review-location').textContent).toBe('/orgs/beta/threads');
      expect(screen.getByRole('dialog')).toBeInTheDocument();
      expect(screen.queryByText('That file name is not allowed.')).toBeNull();
      expect(composeBody).toMatchObject({
        subject: 'Captured subject',
        recipients: ['agent_a'],
        body_markdown: 'Captured body',
      });
    });
  }

  test('a dialog A -> B -> A org return does not let the stale A result touch the new A generation', async () => {
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
    let releaseUpload: (value: unknown) => void = () => {};
    const held = new Promise((resolve) => { releaseUpload = resolve; });
    server.use(
      http.post(`/api/v1/orgs/${SLUG}/artifacts`, async ({ request }) => {
        await held;
        return HttpResponse.json({ name: requestedName(request), size_bytes: 3, modified_at: 'now' });
      }),
      http.post(`/api/v1/orgs/${SLUG}/threads`, async ({ request }) => {
        composeUrls.push(new URL(request.url).pathname);
        await request.json();
        return HttpResponse.json(
          { thread_id: 'THR-NEW', started_at: 'now', pending_replies: 1 },
          { status: 201 },
        );
      }),
    );
    const user = userEvent.setup();
    renderWithProviders(
      <>
        <AppRoutes />
        <NavTo to="/orgs/beta/threads" label="nav-beta-org" testId="nav-beta-org" />
        <NavTo to="/orgs/alpha/threads" label="nav-alpha-org" testId="nav-alpha-org" />
        <LocationProbe />
      </>,
      { route: `/orgs/${SLUG}/threads` },
    );
    await openDialog(user);
    await fillDialog(user, 'First A', 'first body');
    await user.upload(
      screen.getByLabelText(/Attach files/i),
      new File(['abc'], 'note.txt', { type: 'text/plain' }),
    );
    await user.click(screen.getByRole('button', { name: /^Send$/i }));
    // A -> B -> A while the first A upload is held. Each slug change advances
    // the dialog's ownership generation.
    fireEvent.click(document.querySelector('[data-testid="nav-beta-org"]') as HTMLElement);
    await waitFor(() =>
      expect(screen.getByTestId('review-location')).toHaveTextContent('/orgs/beta/threads'),
    );
    fireEvent.click(document.querySelector('[data-testid="nav-alpha-org"]') as HTMLElement);
    await waitFor(() =>
      expect(screen.getByTestId('review-location')).toHaveTextContent('/orgs/alpha/threads'),
    );
    // The reopened-at-alpha dialog is a new generation with empty fields.
    await fillDialog(user, 'Second A', 'second body');
    releaseUpload(undefined);
    await waitFor(() => expect(composeUrls).toHaveLength(1));
    await act(async () => { await new Promise((resolve) => setTimeout(resolve, 60)); });
    // The stale A success must not close, navigate or reset the new A dialog.
    expect(composeUrls[0]).toBe(`/api/v1/orgs/${SLUG}/threads`);
    expect(screen.getByTestId('review-location').textContent).toBe('/orgs/alpha/threads');
    expect(screen.getByRole('dialog')).toBeInTheDocument();
    expect(screen.getByLabelText(/^Subject$/i)).toHaveValue('Second A');
    expect(screen.getByLabelText(/^Body \(Markdown\)$/i)).toHaveValue('second body');
  });

  test('a full dialog unmount abandons the submission and never reopens or navigates', async () => {
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
    const view = renderWithProviders(
      <>
        <AppRoutes />
        <LocationProbe />
      </>,
      { route: `/orgs/${SLUG}/threads` },
    );
    await openDialog(user);
    await fillDialog(user, 'Unmount me', 'held body');
    await user.upload(
      screen.getByLabelText(/Attach files/i),
      new File(['abc'], 'note.txt', { type: 'text/plain' }),
    );
    await user.click(screen.getByRole('button', { name: /^Send$/i }));
    view.unmount();
    // The captured submission still completes for its destination, but the
    // dead dialog must not reopen, navigate or reset anything.
    releaseUpload(undefined);
    await waitFor(() => expect(composeCount).toBe(1));
    await act(async () => { await new Promise((resolve) => setTimeout(resolve, 60)); });
    expect(screen.queryByLabelText(/^Subject$/i)).toBeNull();
    expect(screen.queryByRole('dialog')).toBeNull();
  });

  test('a forward dialog keeps the captured forwarded fields/subject/recipients/body after departure and a prefill change', async () => {
    sessionStorage.setItem('happyranch.token', 'tok');
    stubBaseHandlers();
    stubCreatedThread('THR-NEW');
    let composeBody: Record<string, unknown> | null = null;
    let releaseUpload: (value: unknown) => void = () => {};
    const held = new Promise((resolve) => { releaseUpload = resolve; });
    server.use(
      http.post(`/api/v1/orgs/${SLUG}/artifacts`, async ({ request }) => {
        await held;
        return HttpResponse.json({ name: requestedName(request), size_bytes: 3, modified_at: 'now' });
      }),
      http.post(`/api/v1/orgs/${SLUG}/threads`, async ({ request }) => {
        composeBody = (await request.json()) as Record<string, unknown>;
        return HttpResponse.json(
          { thread_id: 'THR-NEW', started_at: 'now', pending_replies: 1 },
          { status: 201 },
        );
      }),
    );

    const ORIGINAL = {
      subject: 'Fwd: original subject',
      recipients: ['agent_a'],
      body: '> quoted original',
      forwarded_from_id: 'THR-ORIG',
      forwarded_from_kind: 'thread' as const,
    };
    const CHANGED = {
      subject: 'Fwd: changed subject',
      recipients: ['agent_b'],
      body: '> changed',
      forwarded_from_id: 'THR-CHANGED',
      forwarded_from_kind: 'thread' as const,
    };
    function Harness() {
      const [prefill, setPrefill] = useState(ORIGINAL);
      return (
        <>
          <NewThreadDialog
            open
            onClose={() => undefined}
            prefill={prefill}
            onCreated={() => undefined}
            agents={[]}
          />
          <button type="button" data-testid="change-prefill" onClick={() => setPrefill(CHANGED)}>
            change prefill
          </button>
          <NavTo to="/orgs/beta/threads" label="depart" testId="depart" />
        </>
      );
    }

    const user = userEvent.setup();
    renderWithProviders(
      <Routes>
        <Route
          path="/orgs/:slug/threads"
          element={
            <OrgProvider>
              <Harness />
              <LocationProbe />
            </OrgProvider>
          }
        />
      </Routes>,
      { route: `/orgs/${SLUG}/threads` },
    );
    // Prefill seeds the dialog fields.
    await waitFor(() =>
      expect(screen.getByLabelText(/^Subject$/i)).toHaveValue(ORIGINAL.subject),
    );
    await user.upload(
      screen.getByLabelText(/Attach files/i),
      new File(['abc'], 'note.txt', { type: 'text/plain' }),
    );
    await user.click(screen.getByRole('button', { name: /^Send$/i }));
    // Change the prefill while the upload is held: the dialog resets to the new
    // prefill and invalidates the run, but the in-flight compose must still use
    // the fields captured at first submit.
    fireEvent.click(screen.getByTestId('change-prefill'));
    await waitFor(() =>
      expect(screen.getByLabelText(/^Subject$/i)).toHaveValue(CHANGED.subject),
    );
    // Also DEPART the org while the upload is held. The dialog is modal, so
    // dispatch the navigation directly (same as C8.2b).
    fireEvent.click(document.querySelector('[data-testid="depart"]') as HTMLElement);
    await waitFor(() =>
      expect(screen.getByTestId('review-location')).toHaveTextContent('/orgs/beta/threads'),
    );
    releaseUpload(undefined);
    await waitFor(() => expect(composeBody).not.toBeNull());
    await act(async () => { await new Promise((resolve) => setTimeout(resolve, 60)); });
    expect(composeBody).toMatchObject({
      subject: ORIGINAL.subject,
      recipients: ORIGINAL.recipients,
      body_markdown: ORIGINAL.body,
      forwarded_from_id: ORIGINAL.forwarded_from_id,
      forwarded_from_kind: ORIGINAL.forwarded_from_kind,
    });
    // The stale completion must not close, navigate or reset the changed dialog.
    expect(screen.getByRole('dialog')).toBeInTheDocument();
    expect(screen.getByLabelText(/^Subject$/i)).toHaveValue(CHANGED.subject);
    expect(screen.getByTestId('review-location').textContent).toBe('/orgs/beta/threads');
  });
});

/** Freeze the artifact-name timestamp only (timers stay real). */
function freezeArtifactClock(iso = '2026-09-21T00:00:00.000Z') {
  return vi.spyOn(Date.prototype, 'toISOString').mockReturnValue(iso);
}

describe('NewThreadDialog — frozen-clock name collisions (C3.3 new-thread)', () => {
  test('equal-metadata files with different bytes reserve distinct names and never conflate bytes', async () => {
    sessionStorage.setItem('happyranch.token', 'tok');
    stubBaseHandlers();
    stubCreatedThread('THR-NEW');
    const uploadNames: string[] = [];
    let composeBody: unknown = null;
    let composeCount = 0;
    const { parts, restore } = captureFormParts();
    const clock = freezeArtifactClock();
    let attempt = 0;
    server.use(
      http.post(`/api/v1/orgs/${SLUG}/artifacts`, ({ request }) => {
        const name = requestedName(request);
        uploadNames.push(name);
        attempt += 1;
        if (attempt === 2) return HttpResponse.error();
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
    try {
      const user = userEvent.setup();
      renderWithProviders(<AppRoutes />, { route: `/orgs/${SLUG}/threads` });
      await openDialog(user);
      await fillDialog(user, 'Hi');
      // Identical name/size/lastModified, different bytes.
      await user.upload(screen.getByLabelText(/Attach files/i), [
        new File(['AAA'], 'same.txt', { type: 'text/plain', lastModified: 7 }),
        new File(['BBB'], 'same.txt', { type: 'text/plain', lastModified: 7 }),
      ]);
      await user.click(screen.getByRole('button', { name: /^Send$/i }));
      await screen.findByText(/Failed to fetch/i);
      expect(composeCount).toBe(0);
      // Both selections kept distinct chips.
      expect(screen.getAllByRole('button', { name: 'Remove attachment' })).toHaveLength(2);
      await user.click(screen.getByRole('button', { name: /^Send$/i }));
      await waitFor(() => expect(composeCount).toBe(1));

      expect(uploadNames).toHaveLength(3);
      const aName = uploadNames[0];
      const bName = uploadNames[1];
      expect(aName).not.toBe(bName);
      // B is retried under its retained (unchanged reserved) name.
      expect(uploadNames[2]).toBe(bName);
      // Actual multipart parts: A once, B twice, exact retained File names/sizes
      // and B's own bytes on retry (no overwrite/conflation).
      expect(parts.map((p) => p.field)).toEqual(['file', 'file', 'file']);
      expect(parts.map((p) => p.filename)).toEqual([aName, bName, bName]);
      expect(parts.map((p) => p.originalName)).toEqual(['same.txt', 'same.txt', 'same.txt']);
      expect(parts.map((p) => p.size)).toEqual([3, 3, 3]);
      expect(await parts[0].bytes).toEqual([65, 65, 65]);
      expect(await parts[2].bytes).toEqual([66, 66, 66]);
      const refs = (composeBody as { attachments: { artifact_name: string; display_name: string }[] })
        .attachments;
      expect(refs.map((r) => r.artifact_name)).toEqual([aName, bName]);
      expect(refs.map((r) => r.display_name)).toEqual(['same.txt', 'same.txt']);
      // A's exact retained ref (selection order preserved).
      expect(refs[0].artifact_name).toBe(aName);
    } finally {
      restore();
      clock.mockRestore();
    }
  });

  test('sanitized-equal names collide but still reserve distinct artifacts deterministically', async () => {
    sessionStorage.setItem('happyranch.token', 'tok');
    stubBaseHandlers();
    stubCreatedThread('THR-NEW');
    const uploadNames: string[] = [];
    let composeBody: unknown = null;
    let composeCount = 0;
    const { parts, restore } = captureFormParts();
    const clock = freezeArtifactClock();
    let attempt = 0;
    server.use(
      http.post(`/api/v1/orgs/${SLUG}/artifacts`, ({ request }) => {
        const name = requestedName(request);
        uploadNames.push(name);
        attempt += 1;
        if (attempt === 2) return HttpResponse.error();
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
    try {
      const user = userEvent.setup();
      renderWithProviders(<AppRoutes />, { route: `/orgs/${SLUG}/threads` });
      await openDialog(user);
      await fillDialog(user, 'Hi');
      // `a b.txt` and `a-b.txt` both sanitize to `a-b.txt`; the frozen clock
      // makes the sanitized bases collide on the same second.
      await user.upload(screen.getByLabelText(/Attach files/i), [
        new File(['AAA'], 'a b.txt', { type: 'text/plain' }),
        new File(['BBB'], 'a-b.txt', { type: 'text/plain' }),
      ]);
      await user.click(screen.getByRole('button', { name: /^Send$/i }));
      await screen.findByText(/Failed to fetch/i);
      expect(composeCount).toBe(0);
      await user.click(screen.getByRole('button', { name: /^Send$/i }));
      await waitFor(() => expect(composeCount).toBe(1));

      expect(uploadNames).toHaveLength(3);
      const aName = uploadNames[0];
      const bName = uploadNames[1];
      expect(aName).not.toBe(bName);
      // The first selection keeps index 1; the colliding second takes index 2.
      expect(aName).toMatch(/-a-b\.txt$/);
      expect(bName).toMatch(/-2-a-b\.txt$/);
      expect(uploadNames[2]).toBe(bName);
      expect(parts.map((p) => p.filename)).toEqual([aName, bName, bName]);
      expect(await parts[0].bytes).toEqual([65, 65, 65]);
      expect(await parts[2].bytes).toEqual([66, 66, 66]);
      const refs = (composeBody as { attachments: { artifact_name: string }[] }).attachments;
      expect(refs.map((r) => r.artifact_name)).toEqual([aName, bName]);
    } finally {
      restore();
      clock.mockRestore();
    }
  });
});
