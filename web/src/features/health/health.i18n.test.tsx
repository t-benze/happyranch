/**
 * THR-118 W4a-1 — Runtime Health route i18n.
 *
 * Renders the real routed page under the real I18nProvider and asserts:
 *  - zh-CN product copy (header, window toggle, stat cards, loop/HTTP tables,
 *    trends, empty/error states);
 *  - daemon-supplied values (loop names, route keys) stay byte-identical;
 *  - a locale switch keeps the same nodes and the pressed window button's
 *    focus, and issues zero requests.
 */
import { act, fireEvent, screen } from '@testing-library/react';
import { http, HttpResponse } from 'msw';
import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest';
import { AppRoutes } from '@/routes';
import { LocaleTestSwitch, renderWithProviders, savedLocaleAdapter } from '@/test/render';
import { server } from '@/test/server';

const SLUG = 'alpha';
const NOW = Date.parse('2026-10-05T12:00:00Z');

const SNAPSHOT = {
  uptime_seconds: 3 * 3600 + 14 * 60,
  loops: {
    work_hours_scheduler_loop: {
      last_tick_iso: new Date(NOW - 30_000).toISOString(),
      interval_seconds: 60,
      last_duration_seconds: 0.012,
    },
  },
  http: {
    __all__: { count: 120, p50: 0.01, p95: 0.05, max: 0.2 },
    'GET /api/v1/metrics': { count: 3, p50: 0.008, p95: 0.008, max: 0.008 },
  },
  tasks: { pending_and_in_flight: 4 },
  jobs_in_flight: 2,
  executor_sessions_active: 1,
  run_step_queue_depth: 7,
};

function stub(mode: { history?: 'rows' | 'empty' | 'error'; live?: 'ok' | 'error' } = {}) {
  const rows =
    mode.history === 'rows'
      ? [1, 2].map((id) => ({
          id,
          captured_at: new Date(Date.now() - id * 60_000).toISOString(),
          snapshot_json: JSON.stringify({ ...SNAPSHOT, run_step_queue_depth: id }),
        }))
      : [];
  server.use(
    http.get('/api/v1/orgs', () => HttpResponse.json({ orgs: [{ slug: SLUG, root: '/x' }] })),
    http.get('/api/v1/metrics', () =>
      mode.live === 'error'
        ? HttpResponse.json({ detail: 'boom' }, { status: 500 })
        : HttpResponse.json(SNAPSHOT),
    ),
    http.get('/api/v1/metrics/history', () =>
      mode.history === 'error'
        ? HttpResponse.json({ detail: 'boom' }, { status: 500 })
        : HttpResponse.json({ snapshots: rows }),
    ),
  );
}

function mount(locale: 'en' | 'zh-CN') {
  return renderWithProviders(
    <>
      <AppRoutes />
      <LocaleTestSwitch to="zh-CN" />
      <LocaleTestSwitch to="en" />
    </>,
    { route: `/orgs/${SLUG}/health`, i18n: { adapter: savedLocaleAdapter(locale) } },
  );
}

async function switchLocale(to: 'en' | 'zh-CN') {
  await act(async () => {
    fireEvent.click(screen.getByTestId(`test-set-locale-${to}`));
  });
}

async function countRequests(fn: () => Promise<void>): Promise<string[]> {
  const seen: string[] = [];
  const listener = ({ request }: { request: Request }) => {
    seen.push(`${request.method} ${new URL(request.url).pathname}`);
  };
  server.events.on('request:start', listener);
  try {
    await fn();
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 60));
    });
  } finally {
    server.events.removeListener('request:start', listener);
  }
  return seen;
}

beforeEach(() => {
  // Share the fixture's clock with fmtRelTime while MSW/query timers stay real.
  vi.spyOn(Date, 'now').mockReturnValue(NOW);
  sessionStorage.setItem('happyranch.token', 'tok');
  localStorage.clear();
});

afterEach(() => {
  vi.restoreAllMocks();
});

describe('Runtime Health i18n', () => {
  test('populated: zh-CN chrome, verbatim daemon values, same nodes, focus and zero requests across switches', async () => {
    stub({ history: 'rows' });
    mount('zh-CN');

    expect(await screen.findByRole('heading', { name: '运行时健康' })).toBeInTheDocument();
    expect(screen.getByText('实时守护进程指标 · 所有组织')).toBeInTheDocument();
    expect(screen.getByRole('group', { name: '历史窗口' })).toBeInTheDocument();
    expect(await screen.findByText('3 小时 14 分')).toBeInTheDocument();
    expect(screen.getByText('自守护进程启动')).toBeInTheDocument();
    expect(screen.getByText('调度循环')).toBeInTheDocument();
    expect(screen.getByText('HTTP 延迟')).toBeInTheDocument();
    expect(screen.getByText('所有路由')).toBeInTheDocument();
    expect(screen.getByText('60 秒')).toBeInTheDocument();
    expect(screen.getByText('30 秒前')).toBeInTheDocument();
    expect(await screen.findByText('趋势 · 最近 24 小时')).toBeInTheDocument();
    expect(screen.getByText('2 个快照')).toBeInTheDocument();
    // Daemon values stay byte-verbatim.
    expect(screen.getByText('work_hours_scheduler_loop')).toBeInTheDocument();
    expect(screen.getByText('GET /api/v1/metrics')).toBeInTheDocument();

    const loopCell = screen.getByText('work_hours_scheduler_loop');
    const pressed = screen.getByRole('button', { name: '24 小时' });
    pressed.focus();
    const requests = await countRequests(async () => {
      await switchLocale('en');
      expect(screen.getByRole('heading', { name: 'Runtime Health' })).toBeInTheDocument();
      expect(screen.getByText('3h 14m')).toBeInTheDocument();
      expect(screen.getByText('All routes')).toBeInTheDocument();
      expect(screen.getByText('Trends · last 24h')).toBeInTheDocument();
      expect(screen.getByText('2 snapshots')).toBeInTheDocument();
      expect(screen.getByText('work_hours_scheduler_loop')).toBe(loopCell);
      expect(screen.getByRole('button', { name: '24h' })).toBe(pressed);
      expect(document.activeElement).toBe(pressed);
      await switchLocale('zh-CN');
      expect(screen.getByText('运行时健康')).toBeInTheDocument();
      expect(document.activeElement).toBe(pressed);
    });
    expect(requests).toEqual([]);
  });

  test('empty history and live error copy are localized', async () => {
    stub({ history: 'empty', live: 'error' });
    mount('zh-CN');
    expect(
      await screen.findByText('此窗口内尚无持久化快照。守护进程运行时会逐步积累历史。'),
    ).toBeInTheDocument();
    expect(await screen.findByText('无法加载实时指标。正在自动重试…')).toBeInTheDocument();
  });

  test('history error copy is localized', async () => {
    stub({ history: 'error' });
    mount('zh-CN');
    expect(await screen.findByText('无法加载指标历史，请重试。')).toBeInTheDocument();
  });
});
