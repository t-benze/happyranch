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

  it('opens on Cmd-K and renders nothing-loaded fallback', () => {
    setup({ route: '/orgs/demo/threads' });
    expect(screen.queryByRole('dialog')).toBeNull();
    act(() => fireCmdK());
    expect(screen.getByRole('dialog')).toBeInTheDocument();
    expect(
      screen.getByText(/Nothing loaded yet/i),
    ).toBeInTheDocument();
  });

  it('renders sections from cache when present', () => {
    const { getHttpHits } = setup({
      route: '/orgs/demo/threads',
      seed: (qc) => {
        qc.setQueryData(['threads', 'demo', { status: 'open' }], {
          threads: [
            {
              thread_id: 'THR-0001',
              subject: 'Cached thread',
              status: 'open',
            },
          ],
        });
        qc.setQueryData(['tasks', 'demo', {}], {
          tasks: [
            { task_id: 'TASK-1', brief: 'Cached task', team: 'content' },
          ],
        });
      },
    });
    act(() => fireCmdK());
    expect(screen.getByRole('dialog')).toBeInTheDocument();
    expect(screen.getByText('Threads')).toBeInTheDocument();
    expect(screen.getByText(/Cached thread/)).toBeInTheDocument();
    expect(screen.getByText('Tasks')).toBeInTheDocument();
    expect(screen.getByText(/Cached task/)).toBeInTheDocument();
    expect(getHttpHits()).toBe(0);
  });

  it('toggles off on a second Cmd-K', () => {
    setup({ route: '/orgs/demo/threads' });
    act(() => fireCmdK());
    expect(screen.getByRole('dialog')).toBeInTheDocument();
    act(() => fireCmdK());
    expect(screen.queryByRole('dialog')).toBeNull();
  });

  it('reflects cache writes that land while the palette is open', () => {
    const { qc } = setup({ route: '/orgs/demo/threads' });
    act(() => fireCmdK());
    expect(screen.getByText(/Nothing loaded yet/i)).toBeInTheDocument();
    act(() => {
      qc.setQueryData(['threads', 'demo', { status: 'open' }], {
        threads: [
          {
            thread_id: 'THR-9',
            subject: 'Late-arriving thread',
            status: 'open',
          },
        ],
      });
    });
    expect(screen.queryByText(/Nothing loaded yet/i)).toBeNull();
    expect(screen.getByText(/Late-arriving thread/)).toBeInTheDocument();
  });
});
