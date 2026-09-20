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
  /**
   * Reset the module-scoped capacity ordering ledger before mounting.
   *
   * Default `true` so each case starts from a clean sequence. A SECOND
   * consumer mounted against an ALREADY-RUNNING client must pass `false`:
   * resetting the ledger there would erase the very ordering history the
   * during-write cases exist to observe (accepted 2.13).
   */
  resetOrdering?: boolean;
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

  if (options.resetOrdering !== false) resetCapacityOrdering();
  sessionStorage.setItem('happyranch.token', 'tok');
  // Mirror the production `AppProvider` query defaults (AppProvider.tsx:50-58)
  // so cache-lifetime behaviour under test is the behaviour that ships: a
  // remount inside `staleTime` rereads the cache instead of refetching, and a
  // window-focus event is not a read trigger. A venue with `staleTime: 0` /
  // `gcTime: 0` would refetch on every remount and could never observe the
  // cached-remount and focus-suppression cases at all.
  const client = options.client
    ?? new QueryClient({
      defaultOptions: {
        queries: { staleTime: 30_000, refetchOnWindowFocus: false, retry: false },
        mutations: { retry: false },
      },
    });
  const entries = options.entries ?? ['/'];
  const router = createMemoryRouter(
    [{ path: '*', element: <AppProvider client={client}>{ui}</AppProvider> }],
    { initialEntries: entries, initialIndex: options.index ?? entries.length - 1 },
  );
  const view = render(<RouterProvider router={router} />);
  return {
    ...view,
    client,
    router,
    /**
     * Re-render the SAME mounted tree.
     *
     * `rerender(<SomethingElse />)` replaces the router provider and UNMOUNTS
     * the component, so a control missing afterwards proves nothing about a
     * transition. This keeps the component mounted while the hook mock supplies
     * new data (accepted 4.3 / 6.4).
     */
    rerenderSame: () => view.rerender(<RouterProvider router={router} />),
  };
}
