/**
 * THR-118 W3a — founder Dashboard i18n.
 *
 * Renders the real DashboardPage (real data providers, real hooks, msw-backed
 * API) under the real I18nProvider with a saved locale and asserts:
 *   - zh-CN loading, error (+ working Retry), first-run empty and populated
 *     states are localized;
 *   - authored/entity values (agent/team names, task/job/thread ids, question
 *     and job titles, link hrefs) stay byte-for-byte in every locale;
 *   - an en → zh-CN → en switch re-translates in place (same heading node),
 *     with no remount and no API request caused by the switch.
 */
import { afterEach, beforeEach, describe, expect, test } from 'vitest';
import { act, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { delay, http, HttpResponse } from 'msw';
import { Route, Routes } from 'react-router-dom';
import { LocaleTestSwitch, renderWithProviders, savedLocaleAdapter } from '@/test/render';
import { server } from '@/test/server';
import type { DashboardSummaryResponse } from '@/lib/api/types';
import { DashboardPage } from './DashboardPage';

const SLUG = 'hk-macau-tourism';
const ROUTE = `/orgs/${SLUG}/dashboard`;
const SUMMARY_PATH = `/api/v1/orgs/${SLUG}/dashboard/summary`;

function emptySummary(): DashboardSummaryResponse {
  return {
    heartbeat: Array.from({ length: 24 }, (_, h) => ({
      hour: h,
      steps: 0,
      failed: 0,
      tier: 'ok',
    })),
    narrative_counts: {
      completed_today: 0,
      failed_today: 0,
      escalated_open: 0,
      kb_added_today: 0,
      agents_active_now: 0,
      spend_today_usd: 0,
    },
    escalations: [],
    pending_review_jobs: [],
    active_by_team: [],
    recent_activity: [],
    updates_this_week: [],
    org_pulse: [],
    org_age_days: 0,
    server_now: '2026-05-30T12:00:00Z',
    generated_at: '2026-05-30T12:00:00Z',
  };
}

function populatedSummary(): DashboardSummaryResponse {
  const s = emptySummary();
  s.org_age_days = 14;
  s.narrative_counts.completed_today = 5;
  s.narrative_counts.failed_today = 1;
  s.narrative_counts.kb_added_today = 2;
  s.narrative_counts.agents_active_now = 2;
  s.escalations = [
    {
      task_id: 'TASK-101',
      agent: 'qa_engineer',
      team: 'engineering',
      question: 'Photo licensing unclear',
      raised_at: '2026-05-30T11:00:00Z',
      age_seconds: 3600,
      flavor: 'needs-decision',
    },
  ];
  s.pending_review_jobs = [
    {
      id: 'JOB-201',
      task_id: 'TASK-201',
      agent_name: 'dev_agent',
      title: 'Publish founder report',
      created_at: '2026-05-30T11:30:00Z',
    },
  ];
  s.recent_activity = [
    {
      timestamp: '2026-05-30T11:55:00Z',
      who: 'dev_agent',
      event_kind: 'completion_report',
      task_id: 'THR-42',
      verdict: 'ok',
    },
  ];
  s.org_pulse = [
    {
      team: 'engineering',
      acceptance_pct: 87,
      trend_delta: -3,
      sparkline: [0.85, 0.86, 0.87],
      members: 4,
      lead: 'engineering_head',
    },
  ];
  return s;
}

function renderDashboard(locale: 'en' | 'zh-CN') {
  return renderWithProviders(
    <>
      <Routes>
        <Route path="/orgs/:slug/dashboard" element={<DashboardPage />} />
      </Routes>
      <LocaleTestSwitch to="zh-CN" />
      <LocaleTestSwitch to="en" />
    </>,
    { route: ROUTE, i18n: { adapter: savedLocaleAdapter(locale) } },
  );
}

/** Ledger of every request issued; sliced per locale-switch window. */
function recordRequests(): string[] {
  const ledger: string[] = [];
  server.events.on('request:start', ({ request }) => {
    ledger.push(`${request.method} ${new URL(request.url).pathname}${new URL(request.url).search}`);
  });
  return ledger;
}

/** The TODAY narrative paragraph, located through its failed-count clause. */
function narrativeText(failedClause: string): string {
  const clause = screen.getByText(failedClause);
  return clause.parentElement?.textContent ?? '';
}

beforeEach(() => {
  sessionStorage.setItem('happyranch.token', 'tok');
  server.use(
    http.get(`/api/v1/orgs/${SLUG}/tokens`, () => HttpResponse.json({ rollup: [] })),
  );
});

afterEach(() => {
  server.events.removeAllListeners();
});

describe('DashboardPage i18n (W3a)', () => {
  test('zh-CN loading state is localized', async () => {
    server.use(
      http.get(SUMMARY_PATH, async () => {
        await delay(200);
        return HttpResponse.json(emptySummary());
      }),
    );
    renderDashboard('zh-CN');
    expect(await screen.findByText('正在加载仪表盘…')).toBeInTheDocument();
    expect(screen.queryByText('Loading dashboard…')).not.toBeInTheDocument();
  });

  test('zh-CN error state is localized and Retry refetches into the loaded page', async () => {
    let calls = 0;
    server.use(
      http.get(SUMMARY_PATH, () => {
        calls += 1;
        if (calls === 1) return new HttpResponse(null, { status: 500 });
        return HttpResponse.json(populatedSummary());
      }),
    );
    const user = userEvent.setup();
    renderDashboard('zh-CN');

    expect(await screen.findByText('无法加载仪表盘。')).toBeInTheDocument();
    expect(screen.queryByText('Failed to load dashboard.')).not.toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: '重试' }));

    expect(
      await screen.findByRole('heading', { level: 1, name: '有 2 件事需要你处理，创始人' }),
    ).toBeInTheDocument();
    expect(calls).toBe(2);
    expect(screen.queryByText('无法加载仪表盘。')).not.toBeInTheDocument();
  });

  test('zh-CN first-run empty state is localized', async () => {
    server.use(http.get(SUMMARY_PATH, () => HttpResponse.json(emptySummary())));
    renderDashboard('zh-CN');
    expect(await screen.findByText('发出你的第一份简报')).toBeInTheDocument();
    expect(
      screen.getByText(
        '这是你的创始人仪表盘。智能体运行第一个任务后，这里会显示今日动态、等你处理的事项以及各团队的健康状况。',
      ),
    ).toBeInTheDocument();
    expect(screen.queryByText('Start your first brief')).not.toBeInTheDocument();
  });

  test('zh-CN established org with nothing waiting shows the localized all-clear copy', async () => {
    const s = emptySummary();
    s.org_age_days = 3;
    s.narrative_counts.completed_today = 1;
    server.use(http.get(SUMMARY_PATH, () => HttpResponse.json(s)));
    renderDashboard('zh-CN');
    expect(
      await screen.findByRole('heading', { level: 1, name: '事情都处理完了，创始人' }),
    ).toBeInTheDocument();
    expect(screen.getByText('一切就绪')).toBeInTheDocument();
    expect(screen.getByText('没有等待处理的升级或作业。')).toBeInTheDocument();
    expect(screen.getByText('暂无近期动态。')).toBeInTheDocument();
    expect(screen.getByText('尚未配置团队。')).toBeInTheDocument();
    expect(screen.getByText('暂无更新。')).toBeInTheDocument();
  });

  test('zh-CN populated page localizes product copy and keeps entity values verbatim', async () => {
    server.use(http.get(SUMMARY_PATH, () => HttpResponse.json(populatedSummary())));
    renderDashboard('zh-CN');

    expect(
      await screen.findByRole('heading', { level: 1, name: '有 2 件事需要你处理，创始人' }),
    ).toBeInTheDocument();
    const main = screen.getByTestId('dashboard-main');
    const rail = screen.getByTestId('dashboard-rail');
    expect(main).toHaveAttribute('aria-label', '主栏');
    expect(rail).toHaveAttribute('aria-label', '右侧栏');

    // Card titles / meta / actions.
    expect(within(main).getByText('等你处理 · 2')).toBeInTheDocument();
    expect(within(main).getByText('审阅或继续')).toBeInTheDocument();
    expect(within(main).getByText('近期动态')).toBeInTheDocument();
    expect(within(main).getByRole('link', { name: '审阅作业' })).toHaveAttribute(
      'href',
      `/orgs/${SLUG}/jobs/JOB-201`,
    );
    expect(within(rail).getByText('今日')).toBeInTheDocument();
    expect(within(rail).getByText('最近 24 小时')).toBeInTheDocument();
    expect(within(rail).getByText('组织脉搏 · 最近 7 天')).toBeInTheDocument();
    expect(within(rail).getByText('本周消耗')).toBeInTheDocument();
    expect(within(rail).getByText('本周更新')).toBeInTheDocument();
    for (const label of ['已完成', '失败', '活跃', '知识库条目', '今日 Token']) {
      expect(within(rail).getByText(label)).toBeInTheDocument();
    }
    expect(within(rail).getByRole('link', { name: '在用量页面查看 Token 消耗' })).toHaveAttribute(
      'href',
      `/orgs/${SLUG}/spend`,
    );
    expect(within(rail).getByText('4 个智能体')).toBeInTheDocument();
    expect(within(rail).getByLabelText('今日每小时活动')).toBeInTheDocument();

    // Narrative sentence (generated prose with movable slots).
    expect(narrativeText('1 个失败')).toBe(
      '已完成 5 个任务，1 个失败，2 个问题等你处理。知识库新增 2 个条目。',
    );

    // Status/age presentation: mapped display labels, machine values kept.
    expect(within(main).getByText('待决策')).toHaveClass('bg-attention-soft');
    expect(within(main).getByText('成功')).toHaveClass('rounded-full');
    expect(within(main).getByText('5 分钟前')).toBeInTheDocument();
    expect(within(main).getByText('1 小时')).toBeInTheDocument();
    expect(screen.getByText(/0 秒前更新/)).toBeInTheDocument();
    expect(screen.getByText(/第 14 天 · 2 个智能体活跃/)).toBeInTheDocument();

    // Token-threads panel localized once its own fetch resolves.
    expect(await within(rail).findByText('此时间窗口内没有 Token 用量。')).toBeInTheDocument();
    expect(within(rail).getByText('Token 消耗最多的会话 · 7 天')).toBeInTheDocument();
    expect(within(rail).getByRole('group', { name: '时间窗口' })).toBeInTheDocument();

    // Entity values stay byte-for-byte.
    expect(within(main).getByText('qa_engineer')).toBeInTheDocument();
    expect(within(main).getByRole('link', { name: 'TASK-101' })).toHaveAttribute(
      'href',
      `/orgs/${SLUG}/tasks/TASK-101`,
    );
    expect(within(main).getByText('Photo licensing unclear')).toBeInTheDocument();
    expect(within(main).getByText('JOB-201')).toBeInTheDocument();
    expect(within(main).getByText('Publish founder report')).toBeInTheDocument();
    expect(within(main).getByRole('link', { name: 'THR-42' })).toHaveAttribute(
      'href',
      `/orgs/${SLUG}/threads/THR-42`,
    );
    expect(within(rail).getByText('engineering')).toBeInTheDocument();

    // No English product copy leaks.
    for (const english of ['Waiting on you · 2', 'Recent activity', 'Today', 'Review job', 'All clear']) {
      expect(screen.queryByText(english)).not.toBeInTheDocument();
    }
  });

  test('zh-CN escalation expander (placeholder + buttons) is localized', async () => {
    server.use(http.get(SUMMARY_PATH, () => HttpResponse.json(populatedSummary())));
    const user = userEvent.setup();
    renderDashboard('zh-CN');
    await user.click(await screen.findByText('Photo licensing unclear'));
    expect(await screen.findByPlaceholderText('回复 qa_engineer…（⌘↵ 发送）')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '取消' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '继续并解决' })).toBeInTheDocument();
  });

  // Raw audit machine value: an underscore-bearing event_kind sentinel must be
  // rendered byte-for-byte (never `task completed`) in both locales and across
  // both switch directions, while the surrounding product copy retranslates.
  test.each([
    ['en', 'zh-CN'],
    ['zh-CN', 'en'],
  ] as const)('%s → %s → back keeps the raw audit event_kind byte-exact with no request', async (from, to) => {
    const RAW_KIND = 'task_completed';
    const s = populatedSummary();
    s.recent_activity = [{ ...s.recent_activity[0], event_kind: RAW_KIND }];
    server.use(http.get(SUMMARY_PATH, () => HttpResponse.json(s)));
    const ledger = recordRequests();
    renderDashboard(from);

    const title = { en: 'Recent activity', 'zh-CN': '近期动态' } as const;
    const main = await screen.findByTestId('dashboard-main');
    await within(main).findByText(title[from]);
    const rail = screen.getByTestId('dashboard-rail');
    await within(rail).findByText(
      from === 'en' ? 'No token usage in window.' : '此时间窗口内没有 Token 用量。',
    );
    const kind = within(main).getByText(RAW_KIND);
    expect(kind.textContent).toBe(RAW_KIND);
    expect(within(main).queryByText('task completed')).not.toBeInTheDocument();

    const before = ledger.length;
    act(() => screen.getByTestId(`test-set-locale-${to}`).click());
    await waitFor(() => expect(within(main).getByText(title[to])).toBeInTheDocument());
    expect(within(main).getByText(RAW_KIND)).toBe(kind);
    expect(kind.textContent).toBe(RAW_KIND);
    expect(within(main).queryByText('task completed')).not.toBeInTheDocument();

    act(() => screen.getByTestId(`test-set-locale-${from}`).click());
    await waitFor(() => expect(within(main).getByText(title[from])).toBeInTheDocument());
    expect(within(main).getByText(RAW_KIND)).toBe(kind);
    expect(kind.textContent).toBe(RAW_KIND);
    expect(ledger.slice(before)).toEqual([]);
  });

  test('en → zh-CN → en retranslates in place with verbatim entities and no request', async () => {
    server.use(http.get(SUMMARY_PATH, () => HttpResponse.json(populatedSummary())));
    const ledger = recordRequests();
    renderDashboard('en');

    const heading = await screen.findByRole('heading', { level: 1 });
    expect(heading.textContent).toBe('2 things need you, founder');
    // Let every query the page issues settle before the switch windows.
    const rail = screen.getByTestId('dashboard-rail');
    expect(await within(rail).findByText('No token usage in window.')).toBeInTheDocument();
    await waitFor(() => {
      const tile = within(rail).getByText('Tokens today').previousElementSibling;
      expect(tile?.textContent).toBe('0');
    });
    await waitFor(() => {
      const burn = within(rail).getByText("This week's burn").closest('section');
      expect(burn?.textContent).toContain('0');
      expect(burn?.textContent).not.toContain('—');
    });
    expect(narrativeText('1 failed')).toBe(
      '5 tasks completed, 1 failed, 2 questions waiting on you. KB grew by 2 entries.',
    );
    const entityNodes = [
      screen.getByText('qa_engineer'),
      screen.getByText('Photo licensing unclear'),
      screen.getByText('JOB-201'),
      screen.getByText('Publish founder report'),
      screen.getByRole('link', { name: 'THR-42' }),
      screen.getByRole('link', { name: 'TASK-101' }),
    ];
    const entityText = entityNodes.map((n) => n.textContent);

    const before = ledger.length;
    // The ledger is live: the initial load recorded the summary fetch.
    expect(ledger).toContain(`GET ${SUMMARY_PATH}`);
    act(() => screen.getByTestId('test-set-locale-zh-CN').click());
    await waitFor(() => expect(heading.textContent).toBe('有 2 件事需要你处理，创始人'));
    expect(screen.getByRole('heading', { level: 1 })).toBe(heading);
    expect(narrativeText('1 个失败')).toBe(
      '已完成 5 个任务，1 个失败，2 个问题等你处理。知识库新增 2 个条目。',
    );
    expect(screen.getByText('等你处理 · 2')).toBeInTheDocument();
    expect(entityNodes.map((n) => n.isConnected)).toEqual(entityNodes.map(() => true));
    expect(entityNodes.map((n) => n.textContent)).toEqual(entityText);

    act(() => screen.getByTestId('test-set-locale-en').click());
    await waitFor(() => expect(heading.textContent).toBe('2 things need you, founder'));
    expect(screen.getByRole('heading', { level: 1 })).toBe(heading);
    expect(narrativeText('1 failed')).toBe(
      '5 tasks completed, 1 failed, 2 questions waiting on you. KB grew by 2 entries.',
    );
    expect(screen.getByText('Waiting on you · 2')).toBeInTheDocument();
    expect(entityNodes.map((n) => n.textContent)).toEqual(entityText);

    // Neither switch window caused any request.
    expect(ledger.slice(before)).toEqual([]);
  });
});
