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

describe('ArtifactsPage', () => {
  test('lists artifacts and downloads via token-bearing fetch', async () => {
    sessionStorage.setItem('happyranch.token', 'tok');
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

    // jsdom doesn't ship createObjectURL / revokeObjectURL — stub them.
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

    const downloadButton = await screen.findByRole('button', { name: /Download/i });
    expect(screen.getByText('dev_agent-2026-06-09-report.pdf')).toBeInTheDocument();
    expect(screen.getByText('5 MB')).toBeInTheDocument();

    await user.click(downloadButton);

    await waitFor(() => {
      expect(downloadHit).toBe(true);
    });
    const blobArg = createObjectUrlSpy.mock.calls[0]?.[0] as Record<string,unknown> | undefined;
    expect(blobArg).toBeTruthy();
    expect(typeof blobArg!.arrayBuffer).toBe('function');
    expect(blobArg!.type).toBe('application/pdf');
    expect(revokeObjectUrlSpy).toHaveBeenCalledWith(objectUrl);
  });

  test('blocks upload with an invalid name client-side before POSTing', async () => {
    sessionStorage.setItem('happyranch.token', 'tok');
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

    const file = new File(['hi'], 'report.pdf', { type: 'application/pdf' });
    await user.upload(await screen.findByLabelText(/^File$/i), file);
    await user.type(screen.getByLabelText(/^Name/i), 'bad name.pdf');
    await user.click(screen.getByRole('button', { name: /Upload/i }));

    expect(await screen.findByRole('alert')).toHaveTextContent(/letters, digits/i);
    expect(postHit).toBe(false);
  });

  describe('delete', () => {
    afterEach(() => {
      vi.restoreAllMocks();
    });

    test('confirms, deletes, and removes the row on success', async () => {
      sessionStorage.setItem('happyranch.token', 'tok');
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
      sessionStorage.setItem('happyranch.token', 'tok');
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

    test('surfaces an error and keeps the row when delete fails', async () => {
      sessionStorage.setItem('happyranch.token', 'tok');
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
});
