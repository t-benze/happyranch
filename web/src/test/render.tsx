/**
 * Shared test helper: render with a fresh QueryClient + MemoryRouter.
 */
import { QueryClientProvider } from '@tanstack/react-query';
import { render, type RenderOptions, type RenderResult } from '@testing-library/react';
import type { ReactElement } from 'react';
import { MemoryRouter } from 'react-router-dom';
import { makeQueryClient } from '../App';

export function renderWithProviders(
  ui: ReactElement,
  options: { route?: string } & Omit<RenderOptions, 'wrapper'> = {},
): RenderResult {
  const client = makeQueryClient();
  const { route = '/', ...rest } = options;
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={[route]}>{ui}</MemoryRouter>
    </QueryClientProvider>,
    rest,
  );
}
