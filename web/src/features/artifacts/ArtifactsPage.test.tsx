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
  test('lists artifacts with download links via GET /artifacts', async () => {
    sessionStorage.setItem('happyranch.token', 'tok');
    stubBaseHandlers();
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
    );

    renderWithProviders(<AppRoutes />, { route: `/orgs/${SLUG}/artifacts` });

    const link = await screen.findByRole('link', { name: /Download/i });
    expect(link).toHaveAttribute(
      'href',
      '/api/v1/orgs/alpha/artifacts/dev_agent-2026-06-09-report.pdf',
    );
    expect(screen.getByText('dev_agent-2026-06-09-report.pdf')).toBeInTheDocument();
    expect(screen.getByText('5 MB')).toBeInTheDocument();
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
