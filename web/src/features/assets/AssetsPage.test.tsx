import { screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { http, HttpResponse } from 'msw';
import { describe, expect, test } from 'vitest';
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

describe('AssetsPage', () => {
  test('lists assets with download links via GET /artifacts', async () => {
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

    renderWithProviders(<AppRoutes />, { route: `/orgs/${SLUG}/assets` });

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
    renderWithProviders(<AppRoutes />, { route: `/orgs/${SLUG}/assets` });

    const file = new File(['hi'], 'report.pdf', { type: 'application/pdf' });
    await user.upload(await screen.findByLabelText(/^File$/i), file);
    await user.type(screen.getByLabelText(/^Name/i), 'bad name.pdf');
    await user.click(screen.getByRole('button', { name: /Upload/i }));

    expect(await screen.findByRole('alert')).toHaveTextContent(/letters, digits/i);
    expect(postHit).toBe(false);
  });
});
