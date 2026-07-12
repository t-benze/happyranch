import { screen, waitFor } from '@testing-library/react';
import { http, HttpResponse } from 'msw';
import { test, expect } from 'vitest';
import { AppRoutes } from './routes';
import { renderWithProviders } from './test/render';
import { server } from './test/server';

test('root with orgs renders the Sidebar org dropdown after navigate', async () => {
  sessionStorage.setItem('happyranch.token', 'tok');
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
    // Threads page header always renders (THREADS-04 serif title).
    expect(
      screen.getByRole('heading', { name: /Conversations across the org/i }),
    ).toBeInTheDocument();
  });
});

test('root with no orgs redirects to the get-started onboarding surface', async () => {
  sessionStorage.setItem('happyranch.token', 'tok');
  server.use(
    http.get('/api/v1/orgs', () => HttpResponse.json({ orgs: [] })),
    // ConnectRuntimeStep reads prereqs on mount to pre-fill a detected
    // built-in's resolved path; stub it so the surface renders cleanly.
    http.get('/api/v1/health/prereqs', () =>
      HttpResponse.json({ prereqs: [] }),
    ),
  );
  renderWithProviders(<AppRoutes />, { route: '/' });
  // No interstitial: RootRedirect navigates straight to /onboarding, which
  // with existingCount===0 lands on ConnectRuntimeStep (the get-started
  // Connect-runtime surface).
  await waitFor(() =>
    expect(
      screen.getByRole('heading', { name: /Connect your agentic CLI/i }),
    ).toBeInTheDocument(),
  );
});
