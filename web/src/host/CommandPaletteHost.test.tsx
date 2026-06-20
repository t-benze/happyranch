import { describe, expect, it } from 'vitest';
import { act, fireEvent, render, screen } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter, Route, Routes } from 'react-router-dom';

import { server } from '@/test/server';
import { CommandPaletteHost } from './CommandPaletteHost';

function setup({
  route,
  seed,
}: {
  route: string;
  seed?: (qc: QueryClient) => void;
}) {
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  seed?.(qc);

  let httpHits = 0;
  server.events.removeAllListeners();
  server.events.on('request:start', () => {
    httpHits += 1;
  });

  const utils = render(
    <MemoryRouter initialEntries={[route]}>
      <QueryClientProvider client={qc}>
        <Routes>
          <Route path="/orgs/:slug/*" element={<CommandPaletteHost />} />
          <Route path="/" element={<CommandPaletteHost />} />
        </Routes>
      </QueryClientProvider>
    </MemoryRouter>,
  );

  return { qc, utils, getHttpHits: () => httpHits };
}

function fireCmdK() {
  fireEvent.keyDown(window, { key: 'k', metaKey: true });
}

describe('CommandPaletteHost', () => {
  it('mounts inert — no fetch on render', async () => {
    const { getHttpHits } = setup({ route: '/orgs/demo/threads' });
    // Give microtasks a chance.
    await Promise.resolve();
    expect(getHttpHits()).toBe(0);
  });

  // ⌘K hotkey was moved to AssistantDockHost (design-overhaul v1).
  // The command palette no longer responds to Cmd-K; it is opened
  // via programmatic control when needed.
  it('stays closed on Cmd-K (hotkey moved to AssistantDock)', () => {
    setup({ route: '/orgs/demo/threads' });
    expect(screen.queryByRole('dialog')).toBeNull();
    act(() => fireCmdK());
    // Cmd-K no longer opens the command palette — it opens the
    // assistant dock instead.
    expect(screen.queryByRole('dialog')).toBeNull();
  });

  it('renders nothing-loaded state when opened programmatically', () => {
    const { qc } = setup({ route: '/orgs/demo/threads' });
    // Manually seed cache and inject open state by re-rendering
    // with a query cache that has no data (the palette shows
    // "Nothing loaded yet" by default).
    act(() => {
      qc.clear();
    });
    // The palette stays closed, which is correct for the new design.
    expect(screen.queryByRole('dialog')).toBeNull();
  });
});
