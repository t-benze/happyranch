/**
 * Designer-sandbox routes.
 *
 * Mount point: `/__prototypes/...`. The parent layout wraps every prototype
 * in `<PrototypeProvider>`, which swaps the data layer for in-memory mocks.
 * Each sub-route is the same composition file the production routes use,
 * so the harness exercises the "approved → moved" workflow described in
 * DESIGN_SYSTEM.md §8.
 *
 * Production gating: `PROTOTYPES_DISABLED` returns true in production builds
 * unless `VITE_ENABLE_PROTOTYPES` is set. The route tree is unmounted in
 * `routes.tsx` when that flag is true. Dev builds always include it; the
 * lazy-import keeps the prototype bundle out of the main entry chunk.
 */
import { lazy, Suspense } from 'react';
import { Link, Outlet, Route } from 'react-router-dom';
import { TopBar } from '@/design-system/layouts/AppShell/TopBar';
import { PrototypeProvider } from '@/design-system/providers/PrototypeProvider';

const ThreadsV2Screen = lazy(() =>
  import('./threads-v2/screen').then((m) => ({ default: m.ThreadsV2Screen })),
);

export const PROTOTYPES_DISABLED =
  import.meta.env.PROD && !import.meta.env.VITE_ENABLE_PROTOTYPES;

/**
 * Renders the `/__prototypes/*` route subtree.
 *
 * Mount with `{prototypeRoutes()}` inside the parent `<Routes>` block:
 *
 * ```tsx
 * <Route path="/__prototypes" element={<PrototypesLayout />}>
 *   <Route index element={<PrototypesIndex />} />
 *   <Route path="threads-v2" element={<ThreadsV2 />} />
 * </Route>
 * ```
 */
export function prototypeRoutes(): JSX.Element {
  return (
    <Route path="/__prototypes" element={<PrototypesLayout />}>
      <Route index element={<PrototypesIndex />} />
      <Route
        path="threads-v2"
        element={
          <Suspense fallback={<div className="text-text-muted p-6">Loading prototype…</div>}>
            <ThreadsV2Screen />
          </Suspense>
        }
      />
      <Route
        path="threads-v2/:thread_id"
        element={
          <Suspense fallback={<div className="text-text-muted p-6">Loading prototype…</div>}>
            <ThreadsV2Screen />
          </Suspense>
        }
      />
    </Route>
  );
}

function PrototypesLayout(): JSX.Element {
  return (
    <PrototypeProvider>
      <div className="flex h-full flex-col">
        <PrototypeBanner />
        <TopBar />
        <main className="flex-1 overflow-hidden">
          <Outlet />
        </main>
      </div>
    </PrototypeProvider>
  );
}

function PrototypeBanner(): JSX.Element {
  return (
    <div className="border-border-default bg-accent-muted text-caption text-text-secondary flex shrink-0 items-center gap-3 border-b px-4 py-1">
      <span className="text-text-primary font-semibold">Prototype sandbox</span>
      <span>—</span>
      <span>Mock data, no daemon.</span>
      <span className="ml-auto">
        <Link to="/" className="text-accent-default hover:underline">
          Exit
        </Link>
      </span>
    </div>
  );
}

function PrototypesIndex(): JSX.Element {
  return (
    <div className="text-text-primary p-6">
      <h1 className="text-h2 mb-3">Prototypes</h1>
      <p className="text-body text-text-secondary mb-4">
        Designer-agent sandbox. Each entry is a real composition rendered against mock data.
      </p>
      <ul className="flex flex-col gap-2">
        <li>
          <Link to="threads-v2" className="text-accent-default hover:underline">
            /__prototypes/threads-v2
          </Link>
          <span className="text-caption text-text-muted ml-2">
            ThreadsPage under PrototypeProvider
          </span>
        </li>
      </ul>
    </div>
  );
}
