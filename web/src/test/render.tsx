/**
 * Shared test helper: render with a fresh QueryClient + MemoryRouter inside
 * the real `<AppProvider>` (so tests exercise the production data wiring).
 */
import { render, type RenderOptions, type RenderResult } from '@testing-library/react';
import type { ReactElement } from 'react';
import { MemoryRouter } from 'react-router-dom';
import { AppProvider, makeQueryClient } from '@/design-system/providers/AppProvider';

export function renderWithProviders(
  ui: ReactElement,
  options: { route?: string } & Omit<RenderOptions, 'wrapper'> = {},
): RenderResult {
  const client = makeQueryClient();
  const { route = '/', ...rest } = options;
  return render(
    <MemoryRouter initialEntries={[route]}>
      <AppProvider client={client}>{ui}</AppProvider>
    </MemoryRouter>,
    rest,
  );
}
