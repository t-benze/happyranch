/**
 * Capacity-local data-router test mount.
 *
 * `DaemonCapacitySection` calls `useBlocker`, which throws
 * "useBlocker must be used within a data router" under the `MemoryRouter` that
 * the shared `src/test/render.tsx` helper provides — even when the blocker
 * argument is `false`. Every mount of the capacity surface therefore needs a
 * DATA router, not only the navigation cases.
 *
 * The shared helper is deliberately left unchanged: it backs many unrelated
 * feature suites and swapping its router type is an unnecessary cross-feature
 * blast radius. This follows the in-repo precedent at
 * `features/agents/AgentsPage.test.tsx`, the existing test for the only other
 * `useBlocker` page.
 */
import { render } from '@testing-library/react';
import { QueryClient } from '@tanstack/react-query';
import { RouterProvider, createMemoryRouter } from 'react-router-dom';
import type { ReactNode } from 'react';
import { AppProvider } from '@/design-system/providers/AppProvider';
import { resetCapacityOrdering } from '@/design-system/providers/_capacity-ordering';

const NativeRequest = globalThis.Request;

export interface RenderGuardedOptions {
  entries?: string[];
  index?: number;
  client?: QueryClient;
}

/**
 * Mount `ui` under a real data router + `AppProvider`, with a synthetic bearer
 * in `sessionStorage` so the shared client can issue requests against MSW.
 *
 * The capacity ordering ledger is module-scoped by design (it is the provider's
 * own sequence source), so each mount resets it — otherwise one case's sequence
 * numbers would fence the next case's reads.
 */
export function renderGuarded(ui: ReactNode, options: RenderGuardedOptions = {}) {
  // React Router's data-memory history builds a Request with jsdom's
  // AbortSignal, which Node's undici Request rejects. Navigation loaders are
  // not used in this app, so omit that test-environment-only signal.
  globalThis.Request = class RouterTestRequest extends NativeRequest {
    constructor(input: RequestInfo | URL, init?: RequestInit) {
      super(input, init ? { ...init, signal: undefined } : init);
    }
  } as typeof Request;

  resetCapacityOrdering();
  sessionStorage.setItem('happyranch.token', 'tok');
  const client = options.client
    ?? new QueryClient({
      defaultOptions: { queries: { retry: false, gcTime: 0 }, mutations: { retry: false } },
    });
  const entries = options.entries ?? ['/'];
  const router = createMemoryRouter(
    [{ path: '*', element: <AppProvider client={client}>{ui}</AppProvider> }],
    { initialEntries: entries, initialIndex: options.index ?? entries.length - 1 },
  );
  const view = render(<RouterProvider router={router} />);
  return { ...view, client, router };
}
