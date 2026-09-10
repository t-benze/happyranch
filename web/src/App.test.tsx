import { screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
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

test('prototype threads index reaches its inbox and selected detail before the shell inbox destination', async () => {
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
    http.get('/api/v1/orgs', () => HttpResponse.json({ orgs: [] })),
  );
  renderWithProviders(<AppRoutes />, { route: '/__prototypes' });
  const user = userEvent.setup();

  try {
    await waitFor(() => {
      expect(screen.getByText('Prototype sandbox')).toBeInTheDocument();
      expect(screen.getByRole('link', { name: 'Exit' })).toHaveAttribute('href', '/');
      expect(screen.getByRole('navigation', { name: 'Primary navigation' })).toBeInTheDocument();
      expect(screen.queryByLabelText('Open assistant')).not.toBeInTheDocument();
      expect(screen.getByLabelText(/Switch to (light|dark) theme/i)).toBeInTheDocument();
      expect(screen.getByRole('heading', { name: 'Prototypes' })).toBeInTheDocument();
      expect(screen.getByRole('link', { name: '/__prototypes/threads-v2' })).toBeInTheDocument();
    });
    await user.click(screen.getByLabelText(/Switch to (light|dark) theme/i));
    expect(document.documentElement).toHaveAttribute('data-theme', 'dark');
    expect(screen.getByLabelText('Switch to light theme')).toBeInTheDocument();
    await user.click(screen.getByRole('link', { name: '/__prototypes/threads-v2' }));
    await waitFor(() => {
      expect(screen.getByText('Q4 venue research — Macau pavilions')).toBeInTheDocument();
    });
    const selectedThread = screen.getByText('Q4 venue research — Macau pavilions').closest('a');
    expect(selectedThread).toHaveAttribute('href', '/__prototypes/threads-v2/THR-001');
    await user.click(selectedThread!);
    await waitFor(() => expect(screen.getByText(/Short-list draft attached/)).toBeInTheDocument());
    // This is the complete selected-detail phase, before navigation/Exit. The
    // synthetic bootstrap/token fixtures above keep this boundary explicit;
    // they do not imply auth isolation or a zero-network prototype.
    expect(requests).toEqual([
      '/api/v1/auth/bootstrap',
      '/api/v1/orgs/demo-org/tokens?group_by=thread&thread_id=THR-001',
    ]);
    const primaryNavigation = screen.getByRole('navigation', { name: 'Primary navigation' });
    const threadsNavigation = within(primaryNavigation).getByRole('link', { name: 'Threads' });
    expect(threadsNavigation).toHaveAttribute('href', '/__prototypes/threads-v2');
    await user.click(threadsNavigation);
    await waitFor(() => expect(screen.getByRole('heading', { name: /Conversations across the org/i })).toBeInTheDocument());
    expect(screen.queryByText(/Short-list draft attached/)).not.toBeInTheDocument();
    await user.click(screen.getByRole('link', { name: 'Exit' }));
    await waitFor(() => expect(screen.getByRole('heading', { name: /Connect your agentic CLI/i })).toBeInTheDocument());
    expect(screen.queryByText('Prototype sandbox')).not.toBeInTheDocument();
    // The root redirect legitimately reads orgs after Exit; it is outside the
    // selected-detail request array asserted above.
    expect(requests.slice(2)).toEqual(['/api/v1/orgs']);
  } finally {
    server.events.removeListener('request:start', record);
  }
});
