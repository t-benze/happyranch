/**
 * Shared test helper: render with a fresh QueryClient + MemoryRouter inside
 * the real `<AppProvider>` (so tests exercise the production data wiring) and
 * the real `<I18nProvider>` (so tests exercise the production locale wiring).
 */
import { render, type RenderOptions, type RenderResult } from '@testing-library/react';
import type { ReactElement } from 'react';
import { MemoryRouter } from 'react-router-dom';
import { I18nProvider } from '@/hooks/i18n';
import { AppProvider, makeQueryClient } from '@/design-system/providers/AppProvider';
import type { LocaleMode, LocalePreferenceAdapter } from '@/lib/i18n';

export interface I18nTestOptions {
  adapter?: LocalePreferenceAdapter;
  mode?: LocaleMode;
}

export interface RenderWithProvidersOptions extends Omit<RenderOptions, 'wrapper'> {
  route?: string;
  i18n?: I18nTestOptions;
}

export function renderWithProviders(
  ui: ReactElement,
  options: RenderWithProvidersOptions = {},
): RenderResult {
  const client = makeQueryClient();
  const { route = '/', i18n, ...rest } = options;
  return render(
    <MemoryRouter initialEntries={[route]}>
      <I18nProvider adapter={i18n?.adapter} mode={i18n?.mode}>
        <AppProvider client={client}>{ui}</AppProvider>
      </I18nProvider>
    </MemoryRouter>,
    rest,
  );
}
