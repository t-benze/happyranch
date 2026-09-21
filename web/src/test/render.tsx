/**
 * Shared test helper: render with a fresh QueryClient + MemoryRouter inside
 * the real `<AppProvider>` (so tests exercise the production data wiring) and
 * the real `<I18nProvider>` (so tests exercise the production locale wiring).
 */
import { render, type RenderOptions, type RenderResult } from '@testing-library/react';
import type { ReactElement } from 'react';
import { MemoryRouter } from 'react-router-dom';
import { I18nProvider, useI18n } from '@/hooks/i18n';
import { AppProvider, makeQueryClient } from '@/design-system/providers/AppProvider';
import type { Locale, LocaleMode, LocalePreferenceAdapter } from '@/lib/i18n';

export interface I18nTestOptions {
  adapter?: LocalePreferenceAdapter;
  mode?: LocaleMode;
}

/**
 * Reusable test-only preference adapter seeded with a saved locale. It is the
 * non-public control the W2a focused/browser tests use to switch locale; it is
 * never a shipping selector.
 */
export function savedLocaleAdapter(initial: Locale): LocalePreferenceAdapter {
  let current: Locale = initial;
  return {
    id: 'test-saved-locale',
    readSnapshot: () => ({ saved: current }),
    write: (next: Locale) => {
      current = next;
      return { status: 'durable' };
    },
  };
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

/**
 * Test-only locale control. It is the non-shipping switch the W2a focused and
 * browser tests use to change locale mid-session; it renders inside the real
 * provider so state preservation is exercised through the production context.
 */
export function LocaleTestSwitch({ to }: { to: Locale }): JSX.Element {
  const { setLocale } = useI18n();
  return (
    <button type="button" data-testid={`test-set-locale-${to}`} onClick={() => setLocale(to)}>
      switch to {to}
    </button>
  );
}
