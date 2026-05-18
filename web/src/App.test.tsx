import { screen, waitFor } from '@testing-library/react';
import { http, HttpResponse } from 'msw';
import { test, expect } from 'vitest';
import { AppRoutes } from './routes';
import { renderWithProviders } from './test/render';
import { server } from './test/server';

test('root with orgs renders the TopBar org dropdown after navigate', async () => {
  sessionStorage.setItem('grassland.token', 'tok');
  server.use(
    http.get('/api/v1/orgs', () =>
      HttpResponse.json({ orgs: [{ slug: 'alpha', root: '/x' }] }),
    ),
    http.get('/api/v1/orgs/alpha/agents', () =>
      HttpResponse.json({ agents: [] }),
    ),
    http.get('/api/v1/orgs/alpha/threads', () => HttpResponse.json({ threads: [] })),
    http.get('/api/v1/orgs/alpha/threads/events', () =>
      HttpResponse.text('', { headers: { 'content-type': 'text/event-stream' } }),
    ),
  );
  renderWithProviders(<AppRoutes />, { route: '/orgs/alpha/threads' });
  await waitFor(() => {
    expect(screen.getByLabelText(/Active org/i)).toBeInTheDocument();
    // Inbox header always renders on the threads page.
    expect(screen.getByRole('heading', { name: /Inbox/i })).toBeInTheDocument();
  });
});

test('root with no orgs renders the empty-state message', async () => {
  sessionStorage.setItem('grassland.token', 'tok');
  server.use(http.get('/api/v1/orgs', () => HttpResponse.json({ orgs: [] })));
  renderWithProviders(<AppRoutes />, { route: '/' });
  await waitFor(() =>
    expect(screen.getByText(/No orgs loaded/i)).toBeInTheDocument(),
  );
});
