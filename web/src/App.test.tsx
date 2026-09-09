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
    expect(screen.getByLabelText('Open assistant')).toBeInTheDocument();
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

test('prototype threads detail keeps its fixture-assisted shell, navigation, and exit control', async () => {
  const requests: string[] = [];
  const record = ({ request }: { request: Request }) => {
    const url = new URL(request.url);
    requests.push(url.pathname + url.search);
  };
  server.events.on('request:start', record);
  // Fixture-assisted selected-detail evidence: this composition has historically
  // used the direct fresh-token hook, which cold-starts the regular client.
  // These exact responses make the existing request explicit for visual/shell
  // coverage; they are not evidence that the prototype is zero-network.
  server.use(
    http.get('/api/v1/auth/bootstrap', () => HttpResponse.json({ token: 'prototype-test-token' })),
    http.get('/api/v1/orgs/demo-org/tokens', () => HttpResponse.json({ rollup: [] })),
  );
  renderWithProviders(<AppRoutes />, { route: '/__prototypes/threads-v2/THR-001' });

  try {
    await waitFor(() => {
      expect(screen.getByText('Prototype sandbox')).toBeInTheDocument();
      expect(screen.getByRole('link', { name: 'Exit' })).toHaveAttribute('href', '/');
      expect(screen.getByRole('navigation', { name: 'Primary navigation' })).toBeInTheDocument();
      expect(screen.queryByLabelText('Open assistant')).not.toBeInTheDocument();
      expect(screen.getByLabelText(/Switch to (light|dark) theme/i)).toBeInTheDocument();
      expect(screen.getByText('Q4 venue research — Macau pavilions')).toBeInTheDocument();
    });
    await waitFor(() =>
      expect(requests).toEqual([
        '/api/v1/auth/bootstrap',
        '/api/v1/orgs/demo-org/tokens?group_by=thread&thread_id=THR-001',
      ]),
    );
  } finally {
    server.events.removeListener('request:start', record);
  }
});
