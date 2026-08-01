import { screen, within, act } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { http, HttpResponse } from 'msw';
import { Route, Routes } from 'react-router-dom';
import { describe, expect, test, beforeEach, afterEach, vi } from 'vitest';
import type { AssistantStatus } from '@/lib/api/types';
import { renderWithProviders } from '@/test/render';
import { server } from '@/test/server';
import { AssistantSection } from './AssistantSection';

const SLUG = 'alpha';

function stubStatus(status: AssistantStatus) {
  server.use(http.get('/api/v1/assistant/status', () => HttpResponse.json(status)));
}

function render() {
  sessionStorage.setItem('happyranch.token', 'tok');
  renderWithProviders(
    <Routes>
      <Route path="/orgs/:slug/settings/assistant" element={<AssistantSection />} />
    </Routes>,
    { route: `/orgs/${SLUG}/settings/assistant` },
  );
}

describe('AssistantSection (Settings → Assistant)', () => {
  test('configured: shows status and the register form', async () => {
    stubStatus({
      state: 'configured',
      selected_executor: 'claude',
      workspace_path: '/rt/system/assistant/workspace',
      detail: null,
    });

    render();

    expect(await screen.findByText('Configured')).toBeInTheDocument();
    // "claude" also appears in the executor picker, so scope to the status card.
    const statusCard = screen.getByRole('region', { name: /Assistant status/i });
    expect(within(statusCard).getByText('claude')).toBeInTheDocument();
    expect(within(statusCard).getByText('/rt/system/assistant/workspace')).toBeInTheDocument();

    // The full register flow now lives here.
    expect(screen.getByRole('region', { name: /Register executor/i })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /^Register$/i })).toBeInTheDocument();

  });

  test('uninitialized: Initialize prepares the workspace and shows self-registration steps', async () => {
    stubStatus({
      state: 'uninitialized',
      selected_executor: null,
      workspace_path: null,
      detail: null,
    });
    server.use(
      http.post('/api/v1/assistant/init', () =>
        HttpResponse.json({
          state: 'uninitialized',
          selected_executor: null,
          workspace_path: '/rt/system/assistant/workspace',
          detail: null,
        }),
      ),
    );

    const user = userEvent.setup();
    render();

    await user.click(await screen.findByRole('button', { name: /Initialize workspace/i }));

    expect(await screen.findByText(/Self-registration/i)).toBeInTheDocument();
  });

  test('stale_or_broken: shows the detail and a Repair action', async () => {
    stubStatus({
      state: 'stale_or_broken',
      selected_executor: 'codex',
      workspace_path: '/rt/system/assistant/workspace',
      detail: 'workspace missing AGENTS.md',
    });

    render();

    expect(await screen.findByText('Stale or broken')).toBeInTheDocument();
    expect(screen.getByText('workspace missing AGENTS.md')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /^Repair$/i })).toBeInTheDocument();
  });

  test('surfaces the structural registration error verbatim', async () => {
    stubStatus({
      state: 'configured',
      selected_executor: 'claude',
      workspace_path: '/rt/system/assistant/workspace',
      detail: null,
    });
    server.use(
      http.post('/api/v1/assistant/register', () =>
        HttpResponse.json(
          {
            detail: {
              code: 'assistant_executable_not_found',
              executable: 'ghost-cli',
            },
          },
          { status: 400 },
        ),
      ),
    );

    const user = userEvent.setup();
    render();

    await user.type(await screen.findByLabelText(/^Command$/i), 'ghost-cli');
    await user.click(screen.getByRole('button', { name: /^Register$/i }));

    expect(await screen.findByRole('alert')).toHaveTextContent(
      'assistant_executable_not_found',
    );
  });
});

// ---------------------------------------------------------------------------
// THR-078 — Polling removal behavioral regression tests
// ---------------------------------------------------------------------------
// These tests use MSW request counting to prove the polling-removal contracts:
// (1) Stationary surfaces produce zero /assistant/status requests.
// (2) Mounted surfaces produce exactly one fresh request, no interval.
// (3) Mutations write cache correctly and do not trigger a re-fetch.
// ---------------------------------------------------------------------------

describe('Assistant status polling removal — MSW network evidence', () => {
  const STATUS_ROUTE = '/api/v1/assistant/status';

  beforeEach(() => {
    // Clear MSW handlers between tests (server.resetHandlers is called in
    // the global beforeEach via setup). Reset our counter per-test.
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  function countingStatusStub(): { count: () => number; reset: () => void } {
    let count = 0;
    server.use(
      http.get(STATUS_ROUTE, () => {
        count += 1;
        return HttpResponse.json({
          state: 'configured',
          selected_executor: 'claude',
          workspace_path: '/rt/system/assistant/workspace',
          detail: null,
        } satisfies AssistantStatus);
      }),
    );
    return {
      count: () => count,
      reset: () => {
        count = 0;
      },
    };
  }

  test('(4) Assistant settings route mounted → exactly 1 status request, no interval', async () => {
    const counter = countingStatusStub();
    sessionStorage.setItem('happyranch.token', 'tok');

    renderWithProviders(
      <Routes>
        <Route path="/orgs/:slug/settings/assistant" element={<AssistantSection />} />
      </Routes>,
      { route: `/orgs/${SLUG}/settings/assistant` },
    );

    await screen.findByText('Configured');

    // One request fired on mount (enabled=true).
    expect(counter.count()).toBe(1);

    // Fake-timer proof: advance past the old 5 000 ms refetchInterval.
    vi.useFakeTimers();
    await act(() => vi.advanceTimersByTimeAsync(6_000));
    expect(counter.count()).toBe(1);
  });

  test('(6) mutation (repair) caches result via onSuccess setQueryData, no extra GET', async () => {
    // Initial status: stale_or_broken → Repair button should be visible.
    server.use(
      http.get('/api/v1/assistant/status', () =>
        HttpResponse.json({
          state: 'stale_or_broken',
          selected_executor: 'codex',
          workspace_path: '/rt/system/assistant/workspace',
          detail: 'workspace missing AGENTS.md',
        } satisfies AssistantStatus),
      ),
      // Repair returns configured/Codex
      http.post('/api/v1/assistant/repair', () =>
        HttpResponse.json({
          state: 'configured',
          selected_executor: 'codex',
          workspace_path: '/rt/system/assistant/workspace',
          detail: null,
        } satisfies AssistantStatus),
      ),
    );

    const user = userEvent.setup();
    sessionStorage.setItem('happyranch.token', 'tok');
    render();

    // Mount → 1 GET (enabled=true on route).
    await screen.findByText('Stale or broken');
    expect(screen.getByText('workspace missing AGENTS.md')).toBeInTheDocument();

    // Now stub a counter for subsequent requests.
    let getCount = 0;
    server.use(
      http.get('/api/v1/assistant/status', () => {
        getCount += 1;
        return HttpResponse.json({
          state: 'configured',
          selected_executor: 'codex',
          workspace_path: '/rt/system/assistant/workspace',
          detail: null,
        } satisfies AssistantStatus);
      }),
    );

    const repairBtn = screen.getByRole('button', { name: /^Repair$/i });
    expect(repairBtn).toBeInTheDocument();

    // Click Repair.
    await user.click(repairBtn);

    // After repair, onSuccess setQueryData should render the new status
    // WITHOUT an additional GET request.
    await screen.findByText('Configured');
    expect(screen.getByText('codex')).toBeInTheDocument();
    expect(getCount).toBe(0);
  });
});
