/**
 * THR-118 W2a focused shell tests (AppBar helper, root/not-found fallback,
 * ErrorBoundary copy, locale-switch side effects).
 *
 * The mounted shell copy is exercised through the REAL providers
 * (`renderWithProviders`) and the test-only `savedLocaleAdapter` /
 * `LocaleTestSwitch` controls — never a public selector.
 */
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import type { Query } from '@tanstack/react-query';
import { http, HttpResponse } from 'msw';

import { AppRoutes } from '@/routes';
import { pageTitleFromPath } from '@/design-system/layouts/AppShell/AppBar';
import { ErrorBoundary, type ErrorBoundaryCopy } from '@/design-system/layouts/AppShell/ErrorBoundary';
import { AddOrgDialog } from '@/features/orgs/AddOrgDialog';
import { orgs as orgsApi } from '@/lib/api';
import { server } from '@/test/server';
import { LocaleTestSwitch, renderWithProviders, savedLocaleAdapter } from '@/test/render';
import { startupHealth, stubReadyHealth } from '@/test/startup';
import { makeQueryClient } from '@/design-system/providers/AppProvider';
import { HEALTH_QUERY_KEY } from '@/lib/startup';
import { translate } from '@/lib/i18n';
import { StartupStatusBanner } from './StartupStatusBanner';

describe('AppBar.pageTitleFromPath (explicit locale)', () => {
  it('resolves English titles from the URL pathname', () => {
    expect(pageTitleFromPath('/orgs/demo/dashboard', 'en')).toBe('Home');
    expect(pageTitleFromPath('/orgs/demo/health', 'en')).toBe('Runtime Health');
    expect(pageTitleFromPath('/onboarding', 'en')).toBe('Get started');
    expect(pageTitleFromPath('/unknown-surface', 'en')).toBe('Home');
  });

  it('resolves zh-CN titles from the same pathname', () => {
    expect(pageTitleFromPath('/orgs/demo/dashboard', 'zh-CN')).toBe('首页');
    expect(pageTitleFromPath('/orgs/demo/health', 'zh-CN')).toBe('运行状况');
    expect(pageTitleFromPath('/onboarding', 'zh-CN')).toBe('开始使用');
    expect(pageTitleFromPath('/orgs/demo/unknown', 'zh-CN')).toBe('首页');
  });
});

const COPY_EN: ErrorBoundaryCopy = { title: 'EN title', body: 'EN body', retry: 'EN retry' };
const COPY_ZH: ErrorBoundaryCopy = { title: '中文标题', body: '中文说明', retry: '中文重试' };

function Boom({ shouldThrow }: { shouldThrow: boolean }): JSX.Element {
  if (shouldThrow) throw new Error('kaboom detail');
  return <div>safe</div>;
}

describe('ErrorBoundary localized copy (no remount, diagnostics untouched)', () => {
  it('swaps app-owned copy but preserves the captured error and stack bytes', () => {
    const consoleError = vi.spyOn(console, 'error').mockImplementation(() => undefined);
    const { rerender } = render(
      <ErrorBoundary copy={COPY_EN}>
        <Boom shouldThrow />
      </ErrorBoundary>,
    );
    expect(screen.getByText('EN title')).toBeInTheDocument();
    const pre = document.querySelector('pre');
    expect(pre?.textContent).toContain('kaboom detail');
    expect(pre?.textContent).toContain('Error: kaboom detail');
    const stackBefore = pre?.textContent;

    rerender(
      <ErrorBoundary copy={COPY_ZH}>
        <Boom shouldThrow />
      </ErrorBoundary>,
    );
    expect(screen.getByText('中文标题')).toBeInTheDocument();
    expect(screen.getByText('中文说明')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '中文重试' })).toBeInTheDocument();
    // The captured diagnostic bytes are unchanged by the copy swap.
    expect(document.querySelector('pre')?.textContent).toBe(stackBefore);
    consoleError.mockRestore();
  });
});

describe('root fallback copy', () => {
  beforeEach(() => {
    sessionStorage.setItem('happyranch.token', 'fixture-token');
    stubReadyHealth();
  });
  it('renders the English loading copy while the orgs query is pending', async () => {
    const client = makeQueryClient();
    let releaseOrgs!: () => void;
    const gate = new Promise<void>((resolve) => { releaseOrgs = resolve; });
    const handlers: Promise<PromiseSettledResult<unknown>>[] = [];
    const fetches: Promise<PromiseSettledResult<unknown>>[] = [];
    const restores: (() => void)[] = [];
    // QueryCache.add notifies synchronously inside build, before first fetch.
    // Observe only these original instances on this fresh, unseeded client.
    const unsubscribe = client.getQueryCache().subscribe((event) => {
      if (event.type !== 'added') return;
      const query = event.query as Query;
      if (query.queryKey.length !== 1 ||
          (query.queryKey[0] !== 'orgs' && query.queryKey[0] !== HEALTH_QUERY_KEY[0])) return;
      const originalFetch = query.fetch;
      const spy = vi.spyOn(query, 'fetch').mockImplementation((...args) => {
        const original = originalFetch.apply(query, args);
        // Query.promise is only the retryer; retain the complete outer fetch.
        fetches.push(original.then(
          (value) => ({ status: 'fulfilled', value } as const),
          (reason: unknown) => ({ status: 'rejected', reason } as const),
        ));
        return original;
      });
      restores.push(() => spy.mockRestore());
    });
    let view: ReturnType<typeof renderWithProviders> | undefined;
    let primary: { error: unknown } | undefined;
    const later: { phase: string; error: unknown }[] = [];
    try {
      server.use(http.get('/api/v1/orgs', () => {
        const original = (async () => {
          await gate;
          return HttpResponse.json({ orgs: [] });
        })();
        handlers.push(original.then(
          (value) => ({ status: 'fulfilled', value } as const),
          (reason: unknown) => ({ status: 'rejected', reason } as const),
        ));
        return original;
      }));
      view = renderWithProviders(<AppRoutes />, {
        route: '/', queryClient: client,
      });
      // Checking health alone renders the same copy. Reach the original
      // health delivery and its rendered ready state with orgs still pending.
      await waitFor(() => {
        expect(client.getQueryState(HEALTH_QUERY_KEY)?.status).toBe('success');
        expect(screen.queryByRole('status')).not.toBeInTheDocument();
        expect(handlers).toHaveLength(1);
        expect(client.getQueryState(['orgs'])?.status).toBe('pending');
        expect(client.getQueryState(['orgs'])?.fetchStatus).toBe('fetching');
      });
      expect(screen.getByText('Loading…')).toBeInTheDocument();
    } catch (error) {
      primary = { error };
    } finally {
      // Release even if render/assertion failed before the handler arrived.
      releaseOrgs();
      try {
        await act(async () => {
          // Each iterator includes work genuinely started while earlier joins
          // yield. A late-arriving handler is joined after the original fetch.
          for (const completion of fetches) {
            const result = await completion;
            if (result.status === 'rejected') later.push({ phase: 'query fetch', error: result.reason });
          }
          for (const completion of handlers) {
            const result = await completion;
            if (result.status === 'rejected') later.push({ phase: 'orgs handler', error: result.reason });
          }
          // Original fetch delivery schedules notifyManager's zero-delay
          // notifications; let that ordered turn deliver before act flushes.
          await new Promise<void>((resolve) => setTimeout(resolve, 0));
        });
      } catch (error) {
        later.push({ phase: 'React delivery', error });
      }
      try {
        if (view) view.unmount();
        else cleanup(); // render can throw after mounting but before returning.
      } catch (error) {
        later.push({ phase: 'unmount', error });
      }
      try { unsubscribe(); } catch (error) { later.push({ phase: 'unsubscribe', error }); }
      for (const restore of restores) {
        try { restore(); } catch (error) { later.push({ phase: 'restore fetch', error }); }
      }
      try { client.clear(); } catch (error) { later.push({ phase: 'clear cache', error }); }
    }
    if (primary) {
      if (later.length) console.error('Shell pending-query secondary errors', later);
      throw primary.error;
    }
    if (later.length === 1) throw later[0].error;
    if (later.length > 1) throw new AggregateError(later.map(({ error }) => error),
      `Shell pending-query cleanup: ${later.map(({ phase }) => phase).join(', ')}`);
  });

  it('renders the zh-CN loading copy while the orgs query is pending', async () => {
    const client = makeQueryClient();
    let releaseOrgs!: () => void;
    const gate = new Promise<void>((resolve) => { releaseOrgs = resolve; });
    const handlers: Promise<PromiseSettledResult<unknown>>[] = [];
    const fetches: Promise<PromiseSettledResult<unknown>>[] = [];
    const restores: (() => void)[] = [];
    // QueryCache.add notifies synchronously inside build, before first fetch.
    // Observe only these original instances on this fresh, unseeded client.
    const unsubscribe = client.getQueryCache().subscribe((event) => {
      if (event.type !== 'added') return;
      const query = event.query as Query;
      if (query.queryKey.length !== 1 ||
          (query.queryKey[0] !== 'orgs' && query.queryKey[0] !== HEALTH_QUERY_KEY[0])) return;
      const originalFetch = query.fetch;
      const spy = vi.spyOn(query, 'fetch').mockImplementation((...args) => {
        const original = originalFetch.apply(query, args);
        // Query.promise is only the retryer; retain the complete outer fetch.
        fetches.push(original.then(
          (value) => ({ status: 'fulfilled', value } as const),
          (reason: unknown) => ({ status: 'rejected', reason } as const),
        ));
        return original;
      });
      restores.push(() => spy.mockRestore());
    });
    let view: ReturnType<typeof renderWithProviders> | undefined;
    let primary: { error: unknown } | undefined;
    const later: { phase: string; error: unknown }[] = [];
    try {
      server.use(http.get('/api/v1/orgs', () => {
        const original = (async () => {
          await gate;
          return HttpResponse.json({ orgs: [] });
        })();
        handlers.push(original.then(
          (value) => ({ status: 'fulfilled', value } as const),
          (reason: unknown) => ({ status: 'rejected', reason } as const),
        ));
        return original;
      }));
      view = renderWithProviders(<AppRoutes />, {
        route: '/', queryClient: client,
        i18n: { adapter: savedLocaleAdapter('zh-CN') },
      });
      // Checking health alone renders the same copy. Reach the original
      // health delivery and its rendered ready state with orgs still pending.
      await waitFor(() => {
        expect(client.getQueryState(HEALTH_QUERY_KEY)?.status).toBe('success');
        expect(screen.queryByRole('status')).not.toBeInTheDocument();
        expect(handlers).toHaveLength(1);
        expect(client.getQueryState(['orgs'])?.status).toBe('pending');
        expect(client.getQueryState(['orgs'])?.fetchStatus).toBe('fetching');
      });
      expect(screen.getByText('加载中…')).toBeInTheDocument();
    } catch (error) {
      primary = { error };
    } finally {
      // Release even if render/assertion failed before the handler arrived.
      releaseOrgs();
      try {
        await act(async () => {
          // Each iterator includes work genuinely started while earlier joins
          // yield. A late-arriving handler is joined after the original fetch.
          for (const completion of fetches) {
            const result = await completion;
            if (result.status === 'rejected') later.push({ phase: 'query fetch', error: result.reason });
          }
          for (const completion of handlers) {
            const result = await completion;
            if (result.status === 'rejected') later.push({ phase: 'orgs handler', error: result.reason });
          }
          // Original fetch delivery schedules notifyManager's zero-delay
          // notifications; let that ordered turn deliver before act flushes.
          await new Promise<void>((resolve) => setTimeout(resolve, 0));
        });
      } catch (error) {
        later.push({ phase: 'React delivery', error });
      }
      try {
        if (view) view.unmount();
        else cleanup(); // render can throw after mounting but before returning.
      } catch (error) {
        later.push({ phase: 'unmount', error });
      }
      try { unsubscribe(); } catch (error) { later.push({ phase: 'unsubscribe', error }); }
      for (const restore of restores) {
        try { restore(); } catch (error) { later.push({ phase: 'restore fetch', error }); }
      }
      try { client.clear(); } catch (error) { later.push({ phase: 'clear cache', error }); }
    }
    if (primary) {
      if (later.length) console.error('Shell pending-query secondary errors', later);
      throw primary.error;
    }
    if (later.length === 1) throw later[0].error;
    if (later.length > 1) throw new AggregateError(later.map(({ error }) => error),
      `Shell pending-query cleanup: ${later.map(({ phase }) => phase).join(', ')}`);
  });

  it('renders the English not-found copy with an unchanged home href', async () => {
    server.use(http.get('/api/v1/orgs', () => HttpResponse.json({ orgs: [] })));
    renderWithProviders(<AppRoutes />, { route: '/totally-unknown' });
    const link = await screen.findByRole('link', { name: 'Go home' });
    expect(link).toHaveAttribute('href', '/');
    expect(document.body.textContent).toContain('Not found.');
  });

  it('renders the zh-CN not-found copy with the same home href', async () => {
    server.use(http.get('/api/v1/orgs', () => HttpResponse.json({ orgs: [] })));
    renderWithProviders(<AppRoutes />, {
      route: '/totally-unknown',
      i18n: { adapter: savedLocaleAdapter('zh-CN') },
    });
    const link = await screen.findByRole('link', { name: '返回首页' });
    expect(link).toHaveAttribute('href', '/');
    expect(document.body.textContent).toContain('未找到。');
  });
});

describe('startup banner locale and held route', () => {
  it('translates recovering/failed/stopping without fetching or exposing a raw cause', async () => {
    const client = makeQueryClient();
    client.setQueryData(HEALTH_QUERY_KEY, startupHealth('recovering'));
    const requests: string[] = [];
    const record = ({ request }: { request: Request }) => requests.push(request.url);
    server.events.on('request:start', record);
    const view = renderWithProviders(<><StartupStatusBanner /><LocaleTestSwitch to="zh-CN" /></>, {
      queryClient: client, i18n: { adapter: savedLocaleAdapter('en') },
    });
    try {
      expect(screen.getByText(translate('en', 'startup.banner.recovering'))).toBeInTheDocument();
      fireEvent.click(screen.getByTestId('test-set-locale-zh-CN'));
      expect(screen.getByText(translate('zh-CN', 'startup.banner.recovering'))).toBeInTheDocument();
      await act(async () => { client.setQueryData(HEALTH_QUERY_KEY, startupHealth('recovery_failed')); });
      await screen.findByText(translate('zh-CN', 'startup.banner.failed'));
      expect(screen.getByText('daemon_recovery_failed')).toBeInTheDocument();
      await act(async () => { client.setQueryData(HEALTH_QUERY_KEY, startupHealth('stopped')); });
      await screen.findByText(translate('zh-CN', 'startup.banner.stopping'));
      await act(async () => { client.setQueryData(HEALTH_QUERY_KEY, startupHealth()); });
      await waitFor(() => expect(screen.queryByRole('status')).not.toBeInTheDocument());
      expect(requests).toEqual([]);
    } finally {
      server.events.removeListener('request:start', record);
      view.unmount();
      client.clear();
    }
  });

  it('keeps a held effectful page unmounted without eager data calls', async () => {
    sessionStorage.setItem('happyranch.token', 'fixture-token');
    const client = makeQueryClient();
    client.setQueryData(HEALTH_QUERY_KEY, startupHealth('recovering'));
    server.use(http.get('/api/v1/orgs', () => HttpResponse.json({ orgs: [{ slug: 'demo', root: '/x' }] })));
    const requests: string[] = [];
    const record = ({ request }: { request: Request }) => requests.push(new URL(request.url).pathname);
    server.events.on('request:start', record);
    const view = renderWithProviders(<AppRoutes />, { route: '/orgs/demo/health', queryClient: client });
    try {
      expect(screen.getByText(translate('en', 'startup.banner.recovering'))).toBeInTheDocument();
      await act(async () => { await new Promise((resolve) => setTimeout(resolve, 60)); });
      expect(requests.filter((path) => path !== '/api/v1/orgs')).toEqual([]);
      expect(requests).toContain('/api/v1/orgs');
      expect(screen.queryByText('Live daemon metrics · all orgs')).not.toBeInTheDocument();
    } finally {
      server.events.removeListener('request:start', record);
      view.unmount();
      client.clear();
    }
  });
});

describe('locale switch side effects (case C)', () => {
  it('issues no network request and no org mutation on a locale switch', async () => {
    server.events.removeAllListeners();
    let hits = 0;
    server.events.on('request:start', () => {
      hits += 1;
    });
    const createSpy = vi.spyOn(orgsApi, 'createOrg');

    renderWithProviders(
      <>
        <AddOrgDialog open onOpenChange={() => undefined} />
        <LocaleTestSwitch to="zh-CN" />
      </>,
      { route: '/', i18n: { adapter: savedLocaleAdapter('en') } },
    );
    expect(screen.getByText('New org')).toBeInTheDocument();
    // Radix Dialog sets pointer-events:none on outside content while modal, so
    // dispatch the test-only control directly.
    fireEvent.click(screen.getByTestId('test-set-locale-zh-CN'));
    expect(screen.getByText('新建组织')).toBeInTheDocument();
    expect(hits).toBe(0);
    expect(createSpy).not.toHaveBeenCalled();
    server.events.removeAllListeners();
  });
});
