import { screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { http, HttpResponse } from 'msw';
import { afterEach, describe, expect, test, vi } from 'vitest';
import { AppRoutes } from '@/routes';
import { renderWithProviders } from '@/test/render';
import { server } from '@/test/server';

const SLUG = 'alpha';

function stubBaseHandlers() {
  server.use(
    http.get('/api/v1/orgs', () =>
      HttpResponse.json({ orgs: [{ slug: SLUG, root: '/x' }] }),
    ),
  );
}

function seedToken() {
  sessionStorage.setItem('happyranch.token', 'tok');
}

describe('ArtifactsPage', () => {
  /* ------------------------------------------------------------------ */
  /*  Layout                                                            */
  /* ------------------------------------------------------------------ */

  test('renders artifacts in a card grid, not a table', async () => {
    seedToken();
    stubBaseHandlers();
    server.use(
      http.get(`/api/v1/orgs/${SLUG}/artifacts`, () =>
        HttpResponse.json({
          artifacts: [
            { name: 'report.pdf', size_bytes: 5 * 1024 * 1024, modified_at: '2026-06-09T00:00:00Z' },
            { name: 'export.csv', size_bytes: 2048, modified_at: '2026-06-10T12:00:00Z' },
          ],
        }),
      ),
    );

    renderWithProviders(<AppRoutes />, { route: `/orgs/${SLUG}/artifacts` });

    // Cards should appear — no <table> element.
    await screen.findByText('report.pdf');
    expect(screen.queryByRole('table')).not.toBeInTheDocument();

    // Both cards visible
    expect(screen.getByText('report.pdf')).toBeInTheDocument();
    expect(screen.getByText('export.csv')).toBeInTheDocument();

    // Size formatted
    expect(screen.getByText('5 MB')).toBeInTheDocument();
    expect(screen.getByText('2 KB')).toBeInTheDocument();
  });

  /* ------------------------------------------------------------------ */
  /*  Download                                                          */
  /* ------------------------------------------------------------------ */

  test('downloads via token-bearing fetch from a card', async () => {
    seedToken();
    stubBaseHandlers();
    let downloadHit = false;
    server.use(
      http.get(`/api/v1/orgs/${SLUG}/artifacts`, () =>
        HttpResponse.json({
          artifacts: [
            {
              name: 'dev_agent-2026-06-09-report.pdf',
              size_bytes: 5 * 1024 * 1024,
              modified_at: '2026-06-09T00:00:00Z',
            },
          ],
        }),
      ),
      http.get(`/api/v1/orgs/${SLUG}/artifacts/dev_agent-2026-06-09-report.pdf`, ({ request }) => {
        downloadHit = true;
        expect(request.headers.get('Authorization')).toBe('Bearer tok');
        return new HttpResponse('file contents', {
          status: 200,
          headers: { 'Content-Type': 'application/pdf' },
        });
      }),
    );

    const objectUrl = 'blob:fake';
    const createObjectUrlSpy = vi.fn().mockReturnValue(objectUrl);
    const revokeObjectUrlSpy = vi.fn();
    if (!URL.createObjectURL) {
      URL.createObjectURL = createObjectUrlSpy;
      URL.revokeObjectURL = revokeObjectUrlSpy;
    } else {
      vi.spyOn(URL, 'createObjectURL').mockReturnValue(objectUrl);
      vi.spyOn(URL, 'revokeObjectURL').mockImplementation(revokeObjectUrlSpy);
    }

    const user = userEvent.setup();
    renderWithProviders(<AppRoutes />, { route: `/orgs/${SLUG}/artifacts` });

    await screen.findByText('dev_agent-2026-06-09-report.pdf');

    // Find the Download button within the card
    const downloadButton = screen.getByRole('button', { name: /Download/i });
    await user.click(downloadButton);

    await waitFor(() => {
      expect(downloadHit).toBe(true);
    });
    const blobArg = createObjectUrlSpy.mock.calls[0]?.[0] as Blob;
    expect(blobArg.type).toBe('application/pdf');
    expect(blobArg.size).toBeGreaterThan(0);
    expect(revokeObjectUrlSpy).toHaveBeenCalledWith(objectUrl);
  });

  /* ------------------------------------------------------------------ */
  /*  Upload                                                            */
  /* ------------------------------------------------------------------ */

  test('blocks upload with an invalid name client-side before POSTing', async () => {
    seedToken();
    stubBaseHandlers();
    let postHit = false;
    server.use(
      http.get(`/api/v1/orgs/${SLUG}/artifacts`, () =>
        HttpResponse.json({ artifacts: [] }),
      ),
      http.post(`/api/v1/orgs/${SLUG}/artifacts`, () => {
        postHit = true;
        return HttpResponse.json({
          name: 'x',
          size_bytes: 1,
          modified_at: '2026-06-10T00:00:00Z',
        });
      }),
    );

    const user = userEvent.setup();
    renderWithProviders(<AppRoutes />, { route: `/orgs/${SLUG}/artifacts` });

    // Open the upload form
    await user.click(screen.getByRole('button', { name: /Upload$/i }));

    const file = new File(['hi'], 'report.pdf', { type: 'application/pdf' });
    await user.upload(await screen.findByLabelText(/^File$/i), file);
    await user.type(screen.getByLabelText(/^Name/i), 'bad name.pdf');

    // Click the inner Upload button (the submit one)
    const buttons = screen.getAllByRole('button', { name: /Upload/i });
    const submitButton = buttons.find(
      (b) => b.textContent !== 'Upload' || b.closest('section') !== null,
    )!;
    await user.click(submitButton);

    expect(await screen.findByRole('alert')).toHaveTextContent(/letters, digits/i);
    expect(postHit).toBe(false);
  });

  /* ------------------------------------------------------------------ */
  /*  Delete                                                            */
  /* ------------------------------------------------------------------ */

  describe('delete', () => {
    afterEach(() => {
      vi.restoreAllMocks();
    });

    test('confirms, deletes, and removes the card on success', async () => {
      seedToken();
      stubBaseHandlers();
      vi.spyOn(window, 'confirm').mockReturnValue(true);
      let artifacts = [
        { name: 'doomed.pdf', size_bytes: 1024, modified_at: '2026-06-09T00:00:00Z' },
      ];
      let deleteHit = false;
      server.use(
        http.get(`/api/v1/orgs/${SLUG}/artifacts`, () =>
          HttpResponse.json({ artifacts }),
        ),
        http.delete(`/api/v1/orgs/${SLUG}/artifacts/:name`, ({ params }) => {
          deleteHit = true;
          const target = decodeURIComponent(params.name as string);
          artifacts = artifacts.filter((a) => a.name !== target);
          return HttpResponse.json({ name: target, deleted: true });
        }),
      );

      const user = userEvent.setup();
      renderWithProviders(<AppRoutes />, { route: `/orgs/${SLUG}/artifacts` });

      await screen.findByText('doomed.pdf');
      await user.click(screen.getByRole('button', { name: /Delete doomed\.pdf/i }));

      expect(window.confirm).toHaveBeenCalledTimes(1);
      await waitFor(() =>
        expect(screen.queryByText('doomed.pdf')).not.toBeInTheDocument(),
      );
      expect(deleteHit).toBe(true);
    });

    test('does not delete when the confirm is dismissed', async () => {
      seedToken();
      stubBaseHandlers();
      vi.spyOn(window, 'confirm').mockReturnValue(false);
      let deleteHit = false;
      server.use(
        http.get(`/api/v1/orgs/${SLUG}/artifacts`, () =>
          HttpResponse.json({
            artifacts: [
              { name: 'doomed.pdf', size_bytes: 1024, modified_at: '2026-06-09T00:00:00Z' },
            ],
          }),
        ),
        http.delete(`/api/v1/orgs/${SLUG}/artifacts/:name`, () => {
          deleteHit = true;
          return HttpResponse.json({ name: 'doomed.pdf', deleted: true });
        }),
      );

      const user = userEvent.setup();
      renderWithProviders(<AppRoutes />, { route: `/orgs/${SLUG}/artifacts` });

      await screen.findByText('doomed.pdf');
      await user.click(screen.getByRole('button', { name: /Delete doomed\.pdf/i }));

      expect(window.confirm).toHaveBeenCalledTimes(1);
      expect(deleteHit).toBe(false);
      expect(screen.getByText('doomed.pdf')).toBeInTheDocument();
    });

    test('surfaces an error and keeps the card when delete fails', async () => {
      seedToken();
      stubBaseHandlers();
      vi.spyOn(window, 'confirm').mockReturnValue(true);
      server.use(
        http.get(`/api/v1/orgs/${SLUG}/artifacts`, () =>
          HttpResponse.json({
            artifacts: [
              { name: 'doomed.pdf', size_bytes: 1024, modified_at: '2026-06-09T00:00:00Z' },
            ],
          }),
        ),
        http.delete(`/api/v1/orgs/${SLUG}/artifacts/:name`, () =>
          HttpResponse.json(
            { detail: { code: 'artifact_not_found', name: 'doomed.pdf' } },
            { status: 404 },
          ),
        ),
      );

      const user = userEvent.setup();
      renderWithProviders(<AppRoutes />, { route: `/orgs/${SLUG}/artifacts` });

      await screen.findByText('doomed.pdf');
      await user.click(screen.getByRole('button', { name: /Delete doomed\.pdf/i }));

      expect(await screen.findByRole('alert')).toHaveTextContent(/no longer exists/i);
      expect(screen.getByText('doomed.pdf')).toBeInTheDocument();
    });
  });

  /* ------------------------------------------------------------------ */
  /*  States                                                            */
  /* ------------------------------------------------------------------ */

  test('shows loading skeleton while fetching', async () => {
    seedToken();
    stubBaseHandlers();
    // Never resolve — loading stays true.
    server.use(
      http.get(`/api/v1/orgs/${SLUG}/artifacts`, () =>
        new Promise(() => void 0),
      ),
    );

    renderWithProviders(<AppRoutes />, { route: `/orgs/${SLUG}/artifacts` });

    // The skeleton aria-label confirms it renders.
    expect(await screen.findByLabelText('Loading artifacts')).toBeInTheDocument();
  });

  test('shows calm empty state when no artifacts exist', async () => {
    seedToken();
    stubBaseHandlers();
    server.use(
      http.get(`/api/v1/orgs/${SLUG}/artifacts`, () =>
        HttpResponse.json({ artifacts: [] }),
      ),
    );

    renderWithProviders(<AppRoutes />, { route: `/orgs/${SLUG}/artifacts` });

    expect(await screen.findByText('No artifacts yet')).toBeInTheDocument();
    expect(screen.getByText(/Upload a file above/i)).toBeInTheDocument();
  });

  test('shows error with retry button on fetch failure', async () => {
    seedToken();
    stubBaseHandlers();
    server.use(
      http.get(`/api/v1/orgs/${SLUG}/artifacts`, () =>
        HttpResponse.json({ detail: 'boom' }, { status: 500 }),
      ),
    );

    renderWithProviders(<AppRoutes />, { route: `/orgs/${SLUG}/artifacts` });

    expect(await screen.findByText(/Could not load artifacts/)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Retry' })).toBeInTheDocument();
  });

  /* ------------------------------------------------------------------ */
  /*  Honesty lens                                                      */
  /* ------------------------------------------------------------------ */

  test('cards show only stored fields — no fabricated provenance, kind, status, or IDs', async () => {
    seedToken();
    stubBaseHandlers();
    server.use(
      http.get(`/api/v1/orgs/${SLUG}/artifacts`, () =>
        HttpResponse.json({
          artifacts: [
            { name: 'report.pdf', size_bytes: 1024, modified_at: '2026-06-09T00:00:00Z' },
          ],
        }),
      ),
    );

    renderWithProviders(<AppRoutes />, { route: `/orgs/${SLUG}/artifacts` });

    await screen.findByText('report.pdf');

    // Name is present
    expect(screen.getByText('report.pdf')).toBeInTheDocument();
    // Size is present
    expect(screen.getByText('1 KB')).toBeInTheDocument();

    // No TASK- or THR- badges (no stored task_id/thread)
    expect(screen.queryByText(/TASK-/)).not.toBeInTheDocument();
    expect(screen.queryByText(/THR-/)).not.toBeInTheDocument();
    // No agent name chips (no stored agent)
    expect(screen.queryByText(/dev_agent/)).not.toBeInTheDocument();
    // No PR/CI panel or check status
    expect(screen.queryByText(/checks/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/CI/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/merge/i)).not.toBeInTheDocument();
    // No kind pill (no stored kind/type)
    expect(screen.queryByText(/Pull request/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/Doc/i)).not.toBeInTheDocument();
  });
});
