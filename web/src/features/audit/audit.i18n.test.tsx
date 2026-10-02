/**
 * THR-118 W4b — Audit route (page, timeline, filters, narrative) i18n.
 *
 * Renders the real routed page under the real I18nProvider and asserts:
 *  - zh-CN product copy for the header, time-window chips, event-type rail,
 *    clean-record panel, day headers, narrative sentences and detail lines,
 *    plus the empty / all-clear / error states;
 *  - daemon values (agent names, task/thread/job ids, payload values such as
 *    verdicts and statuses) stay byte-identical inside the translated slots,
 *    and entity refs remain links;
 *  - a locale switch keeps the same row/link/filter nodes and focus, leaves
 *    the URL filter params untouched, and issues zero requests;
 *  - the load-error diagnostic boundary: a recognized daemon code maps to
 *    catalog copy, an unknown code and a code-less string detail stay
 *    verbatim, and a detail-less failure shows only the localized headline
 *    (never the synthetic 'API <status>').
 */
import { act, fireEvent, screen, within } from '@testing-library/react';
import { http, HttpResponse } from 'msw';
import { useLocation } from 'react-router-dom';
import { beforeEach, describe, expect, test } from 'vitest';
import { AppRoutes } from '@/routes';
import { LocaleTestSwitch, renderWithProviders, savedLocaleAdapter } from '@/test/render';
import { server } from '@/test/server';

const SLUG = 'alpha';

const ENTRIES = [
  {
    id: 1,
    task_id: 'TASK-1',
    session_id: 's1',
    agent: 'dev_agent',
    action: 'completion_report',
    payload: { status: 'completed', confidence: 90 },
    timestamp: '2026-06-18T10:00:00Z',
  },
  {
    id: 2,
    task_id: 'TASK-2',
    session_id: 's2',
    agent: 'code_reviewer',
    action: 'review_verdict',
    payload: { verdict: 'APPROVE' },
    timestamp: '2026-06-18T09:00:00Z',
  },
  {
    id: 3,
    task_id: 'THR-020',
    session_id: 's3',
    agent: 'engineering_manager',
    action: 'thread_dispatch',
    payload: { task_id: 'TASK-410', target_agent: 'qa_engineer', team: 'engineering' },
    timestamp: '2026-06-17T18:00:00Z',
    _thread_dream_id: 'DREAM-7',
  },
  {
    id: 4,
    task_id: 'TASK-9',
    session_id: 's4',
    agent: null,
    action: 'session_end',
    payload: { duration_seconds: 80, token_usage: { total: 1500 } },
    timestamp: '2026-06-17T17:00:00Z',
  },
];

function LocationProbe(): JSX.Element {
  const loc = useLocation();
  return <span data-testid="probe-search">{loc.search}</span>;
}

function stub(mode: { entries?: unknown[]; error?: { body: unknown; status: number } } = {}) {
  server.use(
    http.get('/api/v1/orgs', () => HttpResponse.json({ orgs: [{ slug: SLUG, root: '/x' }] })),
    http.get(`/api/v1/orgs/${SLUG}/audit`, () =>
      mode.error
        ? HttpResponse.json(mode.error.body as Record<string, unknown>, { status: mode.error.status })
        : HttpResponse.json({ entries: mode.entries ?? ENTRIES, next_cursor: null }),
    ),
  );
}

function mount(locale: 'en' | 'zh-CN', route = `/orgs/${SLUG}/audit`) {
  return renderWithProviders(
    <>
      <AppRoutes />
      <LocationProbe />
      <LocaleTestSwitch to="zh-CN" />
      <LocaleTestSwitch to="en" />
    </>,
    { route, i18n: { adapter: savedLocaleAdapter(locale) } },
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

/** The narrative <p> containing a given entity link. */
function sentenceOf(linkText: string): HTMLElement {
  const link = screen.getByRole('link', { name: linkText });
  const p = link.closest('p');
  if (!p) throw new Error(`no sentence for ${linkText}`);
  return p;
}

beforeEach(() => {
  sessionStorage.setItem('happyranch.token', 'tok');
  localStorage.clear();
});

describe('Audit page chrome i18n', () => {
  test('zh-CN header, time window, rail and clean record; verbatim counts', async () => {
    stub();
    mount('zh-CN');

    expect(await screen.findByRole('heading', { name: '组织的审计记录' })).toBeInTheDocument();
    expect(screen.getByText('仅追加 · 每个操作、执行者与时间')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '导出' })).toBeInTheDocument();
    const window = screen.getByRole('radiogroup', { name: '时间范围' });
    expect(within(window).getByRole('radio', { name: '24 小时' })).toBeInTheDocument();
    expect(within(window).getByRole('radio', { name: '7 天' })).toBeInTheDocument();
    expect(within(window).getByRole('radio', { name: '全部时间' })).toBeInTheDocument();

    const rail = screen.getByRole('complementary', { name: '事件类型筛选' });
    expect(within(rail).getByRole('heading', { name: '事件类型' })).toBeInTheDocument();
    for (const label of ['派发', '已完成', '合并', '升级', '失败']) {
      expect(within(rail).getByText(label)).toBeInTheDocument();
    }
    const completed = within(rail).getByText('已完成').closest('button')!;
    expect(await within(completed).findByText('3')).toBeInTheDocument();

    const clean = screen.getByRole('complementary', { name: '记录良好' });
    expect(within(clean).getByText('记录良好 · 0 次失败')).toBeInTheDocument();
    expect(within(clean).getByText('已记录 4 个事件，均未失败。点击上方的类别即可筛选记录。')).toBeInTheDocument();
  });

  test('class filter: localized label toggles, URL machine value unchanged across a switch, same node + focus, zero requests', async () => {
    stub();
    mount('zh-CN');
    const rail = await screen.findByRole('complementary', { name: '事件类型筛选' });
    const completed = within(rail).getByText('已完成').closest('button')!;
    await act(async () => {
      fireEvent.click(completed);
    });
    expect(completed).toHaveAttribute('aria-pressed', 'true');
    expect(screen.getByTestId('probe-search').textContent).toBe('?class=completed');
    expect(within(rail).getByText('显示全部事件')).toBeInTheDocument();
    const allTime = screen.getByRole('radio', { name: '全部时间' });
    completed.focus();

    const requests = await countRequests(async () => {
      await switchLocale('en');
      expect(within(rail).getByText('Completed').closest('button')).toBe(completed);
      expect(within(rail).getByText('Show all events')).toBeInTheDocument();
      expect(screen.getByRole('radio', { name: 'All time' })).toBe(allTime);
      expect(document.activeElement).toBe(completed);
      expect(screen.getByTestId('probe-search').textContent).toBe('?class=completed');
      await switchLocale('zh-CN');
      expect(within(rail).getByText('已完成').closest('button')).toBe(completed);
      expect(document.activeElement).toBe(completed);
    });
    expect(requests).toEqual([]);
  });
});

describe('Audit timeline i18n', () => {
  test('zh-CN narratives keep daemon values verbatim inside slots and refs as links; zero-request switch keeps nodes', async () => {
    stub();
    mount('zh-CN');

    const timeline = await screen.findByLabelText('审计时间线');
    expect(await within(timeline).findByText('星期四 · 6月18日')).toBeInTheDocument();
    expect(within(timeline).getByText('星期三 · 6月17日')).toBeInTheDocument();

    expect(sentenceOf('TASK-1').textContent).toBe('dev_agent 完成了 TASK-1。');
    expect(within(timeline).getByText('completed · 置信度 90')).toBeInTheDocument();
    expect(sentenceOf('TASK-2').textContent).toBe('code_reviewer 审查了 TASK-2。');
    expect(within(timeline).getByText('APPROVE')).toBeInTheDocument();
    expect(sentenceOf('TASK-410').textContent).toBe('engineering_manager 将 TASK-410 派发给 qa_engineer。');
    expect(screen.getByRole('link', { name: 'qa_engineer' })).toHaveAttribute(
      'href',
      `/orgs/${SLUG}/agents/qa_engineer`,
    );
    expect(within(timeline).getByText('团队 engineering')).toBeInTheDocument();
    expect(sentenceOf('TASK-9').textContent).toBe('系统 结束了 TASK-9。');
    expect(within(timeline).getByText('1 分 20 秒 · 1500 Token')).toBeInTheDocument();
    expect(within(timeline).getByText('来自梦境')).toBeInTheDocument();
    expect(within(timeline).getByLabelText('源自梦境')).toBeInTheDocument();
    expect(within(timeline).getByText('已到审计记录末尾')).toBeInTheDocument();

    const link = screen.getByRole('link', { name: 'TASK-1' });
    const requests = await countRequests(async () => {
      await switchLocale('en');
      expect(sentenceOf('TASK-1').textContent).toBe('dev_agent completed TASK-1.');
      expect(screen.getByRole('link', { name: 'TASK-1' })).toBe(link);
      expect(sentenceOf('TASK-9').textContent).toBe('The system wrapped up TASK-9.');
      expect(screen.getByText('1m 20s · 1.5K tokens')).toBeInTheDocument();
      expect(screen.getByText('THURSDAY · JUN 18')).toBeInTheDocument();
      expect(screen.getByText('from dream')).toBeInTheDocument();
      expect(screen.getByText('End of audit trail')).toBeInTheDocument();
      await switchLocale('zh-CN');
      expect(screen.getByRole('link', { name: 'TASK-1' })).toBe(link);
      expect(sentenceOf('TASK-1').textContent).toBe('dev_agent 完成了 TASK-1。');
    });
    expect(requests).toEqual([]);
  });

  test('empty and all-clear states are localized', async () => {
    stub({ entries: [] });
    const { unmount } = mount('zh-CN');
    expect(await screen.findByText('暂无审计条目')).toBeInTheDocument();
    expect(screen.getByText('没有与当前筛选条件匹配的审计条目。')).toBeInTheDocument();
    unmount();

    stub({ entries: [ENTRIES[0]] });
    mount('zh-CN');
    expect(await screen.findByText('一切正常')).toBeInTheDocument();
    expect(screen.getByText('此时间范围内没有失败或升级。')).toBeInTheDocument();
  });
});

describe('Audit load-error boundary', () => {
  test('recognized daemon code maps to catalog copy and re-translates in place', async () => {
    stub({ error: { body: { detail: { code: 'unknown_org', slug: SLUG } }, status: 404 } });
    mount('zh-CN');
    expect(await screen.findByText('无法加载审计条目。此组织未加载。')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '重试' })).toBeInTheDocument();
    await switchLocale('en');
    expect(screen.getByText("Could not load audit entries. This organization isn't loaded.")).toBeInTheDocument();
  });

  test('unknown code stays verbatim', async () => {
    stub({ error: { body: { detail: { code: 'audit_store_locked' } }, status: 503 } });
    mount('zh-CN');
    expect(await screen.findByText('无法加载审计条目。audit_store_locked')).toBeInTheDocument();
  });

  test('code-less string detail stays verbatim and never shows API <status>', async () => {
    stub({ error: { body: { detail: 'Invalid cursor' }, status: 422 } });
    mount('zh-CN');
    expect(await screen.findByText('无法加载审计条目。Invalid cursor')).toBeInTheDocument();
    expect(screen.queryByText(/API 422/)).toBeNull();
  });

  test('detail-less failure falls back to the localized headline only', async () => {
    stub({ error: { body: { error: 'Internal error' }, status: 500 } });
    mount('zh-CN');
    expect(await screen.findByText('无法加载审计条目。')).toBeInTheDocument();
    expect(screen.queryByText(/API 500/)).toBeNull();
    await switchLocale('en');
    expect(screen.getByText('Could not load audit entries.')).toBeInTheDocument();
  });
});
