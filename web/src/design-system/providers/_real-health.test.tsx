import { act, render, screen, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { http, HttpResponse } from 'msw';
import { beforeEach, describe, expect, it } from 'vitest';
import { server } from '@/test/server';
import { startupHealth } from '@/test/startup';
import { realHealthApi, useRealStartup } from './_real-health';

beforeEach(() => sessionStorage.setItem('happyranch.token', 'fixture-token'));

function Observer({ id }: { id: string }) {
  const status = useRealStartup();
  return <output data-testid={id}>{status.phase}:{String(status.admitted)}</output>;
}

function Poller() {
  realHealthApi.useHealth();
  return <><Observer id="first" /><Observer id="second" /></>;
}

describe('one shared startup observation', () => {
  it('subscribers alone do not fetch or invent readiness', async () => {
    let requests = 0;
    server.use(http.get('/api/v1/health', () => { requests++; return HttpResponse.json(startupHealth()); }));
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    const view = render(<QueryClientProvider client={client}><Observer id="alone" /></QueryClientProvider>);
    await act(async () => { await new Promise((resolve) => setTimeout(resolve, 40)); });
    expect(requests).toBe(0);
    expect(screen.getByTestId('alone')).toHaveTextContent('checking:false');
    view.unmount();
    client.clear();
  });

  it('polls once for all subscribers while held, then changes to the ready cadence', async () => {
    let requests = 0;
    server.use(http.get('/api/v1/health', () => HttpResponse.json(startupHealth(++requests === 1 ? 'recovering' : 'ready'))));
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    const view = render(<QueryClientProvider client={client}><Poller /></QueryClientProvider>);
    try {
      await waitFor(() => expect(screen.getByTestId('first')).toHaveTextContent('recovering:false'));
      expect(screen.getByTestId('second')).toHaveTextContent('recovering:false');
      expect(requests).toBe(1);
      await waitFor(() => expect(screen.getByTestId('first')).toHaveTextContent('ready:true'), { timeout: 3_000 });
      expect(screen.getByTestId('second')).toHaveTextContent('ready:true');
      expect(requests).toBe(2);
      await act(async () => { await new Promise((resolve) => setTimeout(resolve, 2_100)); });
      expect(requests).toBe(2);
    } finally {
      view.unmount();
      client.clear();
    }
  }, 7_000);
});
