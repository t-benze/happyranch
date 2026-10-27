import { act, render, screen, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { http, HttpResponse } from 'msw';
import { StrictMode, useEffect } from 'react';
import { beforeEach, describe, expect, it } from 'vitest';
import { HEALTH_QUERY_KEY, STARTUP_MAX_AGE_MS } from '@/lib/startup';
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

/** A stream-shaped consumer: opens while admitted, closes when admission ends. */
function StreamProbe({ log }: { log: string[] }) {
  const { admitted } = useRealStartup();
  useEffect(() => {
    if (!admitted) return undefined;
    log.push('open');
    return () => { log.push('close'); };
  }, [admitted, log]);
  return null;
}

/** A ready observation whose 90 s freshness ends `msLeft` from now. */
function seedNearlyExpired(client: QueryClient, msLeft: number): number {
  const updatedAt = Date.now() - STARTUP_MAX_AGE_MS + msLeft;
  client.setQueryData(HEALTH_QUERY_KEY, startupHealth(), { updatedAt });
  return updatedAt;
}

describe('shared freshness expiry of the one observation', () => {
  it('expires every mounted subscriber and closes a stream at the deadline while the sole poll is stalled', async () => {
    let requests = 0;
    let release!: () => void;
    const stalled = new Promise<void>((resolve) => { release = resolve; });
    server.use(http.get('/api/v1/health', async () => {
      requests++;
      await stalled;
      return HttpResponse.json(startupHealth());
    }));
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    const updatedAt = seedNearlyExpired(client, 300);
    const log: string[] = [];
    const view = render(<QueryClientProvider client={client}><Poller /><StreamProbe log={log} /></QueryClientProvider>);
    try {
      expect(screen.getByTestId('first')).toHaveTextContent('ready:true');
      expect(screen.getByTestId('second')).toHaveTextContent('ready:true');
      expect(log).toEqual(['open']);
      // No cache write, network response or unrelated render occurs from here on.
      await waitFor(() => expect(screen.getByTestId('first')).toHaveTextContent('unknown:false'), { timeout: 2_000 });
      expect(screen.getByTestId('second')).toHaveTextContent('unknown:false');
      expect(log).toEqual(['open', 'close']);
      expect(requests).toBe(1);
      expect(client.getQueryState(HEALTH_QUERY_KEY)?.dataUpdatedAt).toBe(updatedAt);
    } finally {
      view.unmount();
      release();
      client.clear();
    }
  });

  it('a newer successful observation replaces the deadline without another poll', async () => {
    let requests = 0;
    server.use(http.get('/api/v1/health', () => { requests++; return HttpResponse.json(startupHealth()); }));
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    const original = seedNearlyExpired(client, 300);
    const log: string[] = [];
    const view = render(<QueryClientProvider client={client}><Poller /><StreamProbe log={log} /></QueryClientProvider>);
    try {
      await waitFor(() => expect(client.getQueryState(HEALTH_QUERY_KEY)?.dataUpdatedAt).toBeGreaterThan(original));
      await act(async () => { await new Promise((resolve) => setTimeout(resolve, 600)); });
      expect(screen.getByTestId('first')).toHaveTextContent('ready:true');
      expect(screen.getByTestId('second')).toHaveTextContent('ready:true');
      expect(log).toEqual(['open']);
      expect(requests).toBe(1);
    } finally {
      view.unmount();
      client.clear();
    }
  });

  it('subscribers alone expire without fetching under StrictMode and release the shared owner on unmount', async () => {
    let requests = 0;
    server.use(http.get('/api/v1/health', () => { requests++; return HttpResponse.json(startupHealth()); }));
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    seedNearlyExpired(client, 300);
    const view = render(
      <StrictMode><QueryClientProvider client={client}><Observer id="a" /><Observer id="b" /></QueryClientProvider></StrictMode>,
    );
    try {
      expect(screen.getByTestId('a')).toHaveTextContent('ready:true');
      expect(client.getQueryCache().hasListeners()).toBe(true);
      await waitFor(() => expect(screen.getByTestId('a')).toHaveTextContent('unknown:false'), { timeout: 2_000 });
      expect(screen.getByTestId('b')).toHaveTextContent('unknown:false');
      expect(requests).toBe(0);
    } finally {
      view.unmount();
    }
    expect(client.getQueryCache().hasListeners()).toBe(false);
    client.clear();
  });
});
