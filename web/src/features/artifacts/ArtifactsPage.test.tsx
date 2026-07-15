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

    // The card title is the slug after the <agent>-<date>- prefix is stripped.
    await screen.findByText('report.pdf');

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

  test('header Upload action is a filled primary pill when closed and flips to a quiet ghost Cancel when the form is open (THR-099 Batch 3)', async () => {
    seedToken();
    stubBaseHandlers();
    server.use(
      http.get(`/api/v1/orgs/${SLUG}/artifacts`, () =>
        HttpResponse.json({ artifacts: [] }),
      ),
    );

    const user = userEvent.setup();
    renderWithProviders(<AppRoutes />, { route: `/orgs/${SLUG}/artifacts` });

    // CLOSED state: the header action reads "Upload" and renders in the
    // primary FILLED-PILL variant (Button variant="default" → bg-primary),
    // per the a-artifacts reference. The old always-ghost Upload button had
    // no bg-primary, so this assertion red-proofs the variant switch.
    const uploadToggle = await screen.findByRole('button', { name: 'Upload' });
    expect(uploadToggle).toHaveClass('bg-primary');

    // Toggling the form OPEN flips the SAME control to the quiet GHOST
    // "Cancel" dismiss state — it must no longer read as a primary action.
    await user.click(uploadToggle);
    const cancelToggle = await screen.findByRole('button', { name: 'Cancel' });
    expect(cancelToggle).not.toHaveClass('bg-primary');
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
  /*  Header (ART-03)                                                    */
  /* ------------------------------------------------------------------ */

  test('renders the eyebrow (artifact + distinct-thread counts) and serif title', async () => {
    seedToken();
    stubBaseHandlers();
    server.use(
      http.get(`/api/v1/orgs/${SLUG}/artifacts`, () =>
        HttpResponse.json({
          artifacts: [
            { name: 'dev_agent-2026-06-11-THR-010-a.md', size_bytes: 10, modified_at: '2026-06-11T00:00:00Z' },
            { name: 'qa_engineer-2026-06-12-THR-021-b.md', size_bytes: 20, modified_at: '2026-06-12T00:00:00Z' },
            { name: 'plain-note.txt', size_bytes: 30, modified_at: '2026-06-13T00:00:00Z' },
          ],
        }),
      ),
    );

    renderWithProviders(<AppRoutes />, { route: `/orgs/${SLUG}/artifacts` });

    expect(await screen.findByText('Everything the org has produced')).toBeInTheDocument();
    // 3 artifacts, 2 distinct THR ids (await — the eyebrow renders once data loads).
    expect(
      await screen.findByText(/3 artifacts · produced by 2 threads/i),
    ).toBeInTheDocument();
  });

  /* ------------------------------------------------------------------ */
  /*  Derived type pill + provenance (ART-01) — honesty boundary         */
  /* ------------------------------------------------------------------ */

  test('derives type pill + provenance from the name; never fabricates for non-convention names', async () => {
    seedToken();
    stubBaseHandlers();
    server.use(
      http.get(`/api/v1/orgs/${SLUG}/artifacts`, () =>
        HttpResponse.json({
          artifacts: [
            // Matches the convention + carries a THR token.
            { name: 'dev_agent-2026-06-16-THR-030-handoff.md', size_bytes: 1024, modified_at: '2026-06-16T00:00:00Z' },
            // Does NOT match the convention — provenance must stay neutral.
            { name: 'report.pdf', size_bytes: 1024, modified_at: '2026-06-09T00:00:00Z' },
          ],
        }),
      ),
    );

    renderWithProviders(<AppRoutes />, { route: `/orgs/${SLUG}/artifacts` });

    // Derived type pill (document) appears for the .md artifact.
    expect(await screen.findAllByText('document')).not.toHaveLength(0);
    // Provenance parsed from the name: THR link + agent + formatted date.
    expect(screen.getByText('THR-030')).toBeInTheDocument();
    expect(screen.getByText('dev_agent')).toBeInTheDocument();
    expect(screen.getByText('Jun 16, 2026')).toBeInTheDocument();

    // The non-convention 'report.pdf' shows no fabricated agent/THR/date.
    expect(screen.getByText('report.pdf')).toBeInTheDocument();
    expect(screen.queryByText(/TASK-/)).not.toBeInTheDocument();

    // The deferred status pill is NEVER rendered (no data source).
    for (const status of ['merged', 'draft', 'open', 'final', 'applied']) {
      expect(screen.queryByText(status)).not.toBeInTheDocument();
    }
  });

  /* ------------------------------------------------------------------ */
  /*  Filter + sort (ART-02)                                             */
  /* ------------------------------------------------------------------ */

  test('the type filter narrows the grid to a single derived category', async () => {
    seedToken();
    stubBaseHandlers();
    server.use(
      http.get(`/api/v1/orgs/${SLUG}/artifacts`, () =>
        HttpResponse.json({
          artifacts: [
            { name: 'notes.md', size_bytes: 10, modified_at: '2026-06-11T00:00:00Z' },
            { name: 'mock.png', size_bytes: 20, modified_at: '2026-06-12T00:00:00Z' },
          ],
        }),
      ),
    );

    const user = userEvent.setup();
    renderWithProviders(<AppRoutes />, { route: `/orgs/${SLUG}/artifacts` });

    await screen.findByText('notes.md');
    expect(screen.getByText('mock.png')).toBeInTheDocument();

    // Filter to Designs — the doc drops out, the image stays.
    await user.click(screen.getByRole('tab', { name: 'Designs' }));
    expect(screen.getByText('mock.png')).toBeInTheDocument();
    expect(screen.queryByText('notes.md')).not.toBeInTheDocument();
  });

  test('sorts the grid by modified_at, most recent first', async () => {
    seedToken();
    stubBaseHandlers();
    server.use(
      http.get(`/api/v1/orgs/${SLUG}/artifacts`, () =>
        HttpResponse.json({
          artifacts: [
            { name: 'older.md', size_bytes: 10, modified_at: '2026-06-01T00:00:00Z' },
            { name: 'newer.md', size_bytes: 20, modified_at: '2026-06-20T00:00:00Z' },
          ],
        }),
      ),
    );

    renderWithProviders(<AppRoutes />, { route: `/orgs/${SLUG}/artifacts` });

    await screen.findByText('newer.md');
    const titles = screen
      .getAllByRole('heading', { level: 3 })
      .map((h) => h.textContent);
    expect(titles).toEqual(['newer.md', 'older.md']);
  });

  /* ------------------------------------------------------------------ */
  /*  Folder tree + breadcrumb (THR-061 slice 8) — client-derived        */
  /* ------------------------------------------------------------------ */

  function stubFolderedArtifacts() {
    server.use(
      http.get(`/api/v1/orgs/${SLUG}/artifacts`, () =>
        HttpResponse.json({
          artifacts: [
            { name: 'reports/code_reviewer-2026-06-15-notes.md', size_bytes: 1024, modified_at: '2026-06-15T00:00:00Z' },
            { name: 'reports/qa/qa_engineer-2026-06-14-parity.md', size_bytes: 2048, modified_at: '2026-06-14T00:00:00Z' },
            { name: 'release-checklist.md', size_bytes: 512, modified_at: '2026-06-16T00:00:00Z' },
          ],
        }),
      ),
    );
  }

  test('groups foldered names into a subfolder row + shows only root files at the root', async () => {
    seedToken();
    stubBaseHandlers();
    stubFolderedArtifacts();

    renderWithProviders(<AppRoutes />, { route: `/orgs/${SLUG}/artifacts` });

    // The root-level flat file is a card; the foldered files are hidden under a folder.
    expect(await screen.findByText('release-checklist.md')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /reports\/\s*2 files/i })).toBeInTheDocument();
    expect(screen.queryByText('notes.md')).not.toBeInTheDocument();
    expect(screen.queryByText('parity.md')).not.toBeInTheDocument();
    // Breadcrumb root is present (folders exist).
    expect(screen.getByRole('navigation', { name: /breadcrumb/i })).toBeInTheDocument();
  });

  test('navigates into a folder and back out via the breadcrumb', async () => {
    seedToken();
    stubBaseHandlers();
    stubFolderedArtifacts();

    const user = userEvent.setup();
    renderWithProviders(<AppRoutes />, { route: `/orgs/${SLUG}/artifacts` });

    await screen.findByRole('button', { name: /reports\/\s*2 files/i });
    await user.click(screen.getByRole('button', { name: /reports\/\s*2 files/i }));

    // Inside reports: the direct file shows, a deeper subfolder appears, the root file is gone.
    expect(await screen.findByText('notes.md')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /qa\/\s*1 file/i })).toBeInTheDocument();
    expect(screen.queryByText('release-checklist.md')).not.toBeInTheDocument();

    // Climb back to the root via the breadcrumb.
    await user.click(screen.getByRole('button', { name: 'Artifacts' }));
    expect(await screen.findByText('release-checklist.md')).toBeInTheDocument();
    expect(screen.queryByText('notes.md')).not.toBeInTheDocument();
  });

  test('flat-only names render with no breadcrumb or folder chrome', async () => {
    seedToken();
    stubBaseHandlers();
    server.use(
      http.get(`/api/v1/orgs/${SLUG}/artifacts`, () =>
        HttpResponse.json({
          artifacts: [
            { name: 'a.md', size_bytes: 10, modified_at: '2026-06-11T00:00:00Z' },
            { name: 'b.txt', size_bytes: 20, modified_at: '2026-06-12T00:00:00Z' },
          ],
        }),
      ),
    );

    renderWithProviders(<AppRoutes />, { route: `/orgs/${SLUG}/artifacts` });

    await screen.findByText('a.md');
    expect(screen.queryByRole('navigation', { name: /breadcrumb/i })).not.toBeInTheDocument();
    expect(screen.getByText('Recent first')).toBeInTheDocument();
  });
});
