import { useState } from 'react';
import { createBrowserRouter, RouterProvider } from 'react-router-dom';
import { I18nProvider } from '@/hooks/i18n';
import { AppProvider, makeQueryClient } from '@/design-system/providers/AppProvider';
import type { LocaleResolution } from '@/lib/i18n';
import { AppRoutes } from './routes';

export { makeQueryClient };

export interface AppProps {
  /**
   * The single synchronous startup resolution from
   * `bootstrapDocumentLocale` (production `main.tsx`). Handing it through means
   * `<html lang>` and the first React text share one snapshot even if the
   * adapter's snapshot changes between reads. Direct tests may omit it and let
   * `<I18nProvider>` resolve on its own.
   */
  initialLocale?: LocaleResolution;
}

/**
 * The production provider/route composition, separated from the browser data
 * router so the exact same element can be mounted directly under a memory
 * router in tests. `App` mounts it under `createBrowserRouter`; `main.tsx`
 * hands `App` the one startup resolution.
 */
export function AppShell({ initialLocale }: AppProps = {}): JSX.Element {
  return (
    <I18nProvider initialResolution={initialLocale}>
      <AppProvider>
        <AppRoutes />
      </AppProvider>
    </I18nProvider>
  );
}

export function App({ initialLocale }: AppProps = {}): JSX.Element {
  const [router] = useState(() =>
    createBrowserRouter([{ path: '*', element: <AppShell initialLocale={initialLocale} /> }]),
  );

  return <RouterProvider router={router} />;
}
