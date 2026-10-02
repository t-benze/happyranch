/**
 * THR-118 W4c — Agents route family (list + detail + team escalation policy
 * page + owned dialogs) i18n.
 *
 * Renders the real routed pages under the real I18nProvider and asserts:
 *  - zh-CN product chrome on the roster, the detail pane, the pending
 *    enrollments tab, AddAgentDialog, the reject dialog and the team
 *    escalation policy page (incl. its confirm/discard dialogs);
 *  - every owned dialog's close button is named by `common.close` (关闭);
 *  - daemon values (agent names, roles, teams, descriptions, models,
 *    executors, policy bodies, task ids, statuses) stay byte-verbatim;
 *  - the count-bearing tasks unit is a plural catalog entry;
 *  - the classifyAgentError diagnostic boundary.
 */
import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { QueryClient } from '@tanstack/react-query';
import { http, HttpResponse } from 'msw';
import { createMemoryRouter, RouterProvider } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, test } from 'vitest';
import { AppRoutes } from '@/routes';
import { AppProvider } from '@/design-system/providers/AppProvider';
import { I18nProvider } from '@/hooks/i18n';
import { LocaleTestSwitch, renderWithProviders, savedLocaleAdapter } from '@/test/render';
import { server } from '@/test/server';
import { translate } from '@/lib/i18n';
import { en, zhCN } from '@/lib/i18n/catalog';
import { classifyAgentError, renderAgentError } from './strings';

const SLUG = 'happyranch';
const API = '/api/v1';
const NativeRequest = globalThis.Request;

const AGENTS = {
  agents: [
    {
      name: 'engineering_manager', team: 'engineering', role: 'manager', executor: 'claude',
      model: 'claude-sonnet-4-20250514', description: 'Owns engineering delivery.',
      repos: { happyranch: 'https://github.com/t-benze/happyranch' },
      system_prompt: 'You are the engineering manager.',
    },
    {
      name: 'support_agent', team: 'cx', role: 'worker', executor: 'codex', model: null,
      description: 'Handles support.', repos: {}, system_prompt: 'You are support.',
    },
  ],
};

const POLICY = {
  team: 'engineering', target_manager: 'engineering_manager', can_mutate: true,
  bootstrap_required: true, family: 'empty', selector_id: `APS-${'a'.repeat(64)}`, selector_epoch: 0,
  bootstrap_template: {
    title: 'Legacy', normative_text: 'Normative', clauses: [],
    continuation_phrase: 'routine same-root follow-through',
  },
  v2_starter: {
    policy_id: 'team-8c85b6639e62e10b-dual-text', title: 'Engineering escalation policy',
    what_to_escalate: 'Escalate scope changes verbatim.', what_not_to_escalate: 'Continue ordinary work verbatim.',
  },
};

function stub(mode: { enrollmentsError?: { status: number; body: Record<string, string> } } = {}) {
  server.use(
    http.get(`${API}/auth/bootstrap`, () => HttpResponse.json({ token: 'tok' })),
    http.get(`${API}/orgs`, () => HttpResponse.json({ orgs: [{ slug: SLUG, root: true }], broken: [] })),
    http.get(`${API}/orgs/${SLUG}/agents`, () => HttpResponse.json(AGENTS)),
    http.get(`${API}/orgs/${SLUG}/teams`, () =>
      HttpResponse.json({ teams: [{ name: 'engineering', manager: 'engineering_manager' }] }),
    ),
    http.get(`${API}/health/prereqs`, () => HttpResponse.json({
      prereqs: [
        { tool: 'claude', present: true, path: '/usr/bin/claude', hint: '' },
        { tool: 'codex', present: true, path: '/usr/bin/codex', hint: '' },
        { tool: 'pi', present: false, path: null, hint: '' },
      ],
    })),
    http.get(`${API}/executors/runtime/profiles`, () => HttpResponse.json({ profiles: [] })),
    http.get(`${API}/orgs/${SLUG}/tasks`, () => HttpResponse.json({ tasks: [] })),
    http.get(`${API}/orgs/${SLUG}/agents/:agent/memory/entries/`, () => HttpResponse.json({ entries: [] })),
    http.get(`${API}/orgs/${SLUG}/jobs/`, () => HttpResponse.json({ jobs: [] })),
    http.get(`${API}/orgs/${SLUG}/agents/engineering_manager/cleanup-activity`, () =>
      HttpResponse.json({ activities: [{
        task_id: 'TASK-CLEANUP-9', status: 'completed', result_status: 'blocked',
        created_at: '2026-06-10T12:00:00Z', output_summary: 'Removed two stale worktrees.',
      }] }),
    ),
    http.get(`${API}/orgs/${SLUG}/agents/enrollments`, () =>
      mode.enrollmentsError
        ? HttpResponse.json(mode.enrollmentsError.body, { status: mode.enrollmentsError.status })
        : HttpResponse.json({ enrollments: [{
          name: 'new_writer', team: 'content', role: 'worker', executor: 'claude',
          description: 'Drafts long-form posts.', status: 'pending', enrolled_by: 'content_manager',
          created_at: '2026-05-18T19:00:00Z',
        }] }),
    ),
    http.get(`${API}/orgs/${SLUG}/agents/engineering_manager/team-escalation-policy`, () => HttpResponse.json(POLICY)),
    http.get(`${API}/orgs/${SLUG}/agents/engineering_manager/team-escalation-policy/v2/history`, () =>
      HttpResponse.json({ items: [], next_cursor: null })),
    http.all(`${API}/*`, () => HttpResponse.json({})),
  );
}

function mount(locale: 'en' | 'zh-CN', route: string) {
  return renderWithProviders(
    <>
      <AppRoutes />
      <LocaleTestSwitch to="zh-CN" />
      <LocaleTestSwitch to="en" />
    </>,
    { route, i18n: { adapter: savedLocaleAdapter(locale) } },
  );
}

/** The policy page uses useBlocker, which needs a data router. */
function mountPolicy(locale: 'en' | 'zh-CN') {
  globalThis.Request = class RouterTestRequest extends NativeRequest {
    constructor(input: RequestInfo | URL, init?: RequestInit) {
      super(input, init ? { ...init, signal: undefined } : init);
    }
  };
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const router = createMemoryRouter([{
    path: '*',
    element: (
      <I18nProvider adapter={savedLocaleAdapter(locale)}>
        <AppProvider client={client}><AppRoutes /></AppProvider>
      </I18nProvider>
    ),
  }], { initialEntries: [`/orgs/${SLUG}/agents/engineering_manager/team-escalation-policy`] });
  return render(<RouterProvider router={router} />);
}

function dialogOf(heading: HTMLElement): HTMLElement {
  const dialog = heading.closest('[role="dialog"]');
  if (!(dialog instanceof HTMLElement)) throw new Error('heading is not inside a dialog');
  return dialog;
}

beforeEach(() => {
  sessionStorage.setItem('happyranch.token', 'tok');
  localStorage.clear();
});

afterEach(() => {
  globalThis.Request = NativeRequest;
});

describe('Agents roster + detail i18n', () => {
  test('zh-CN chrome on the roster and detail pane, daemon values verbatim', async () => {
    stub();
    mount('zh-CN', `/orgs/${SLUG}/agents/engineering_manager`);

    expect(await screen.findByRole('button', { name: '发起会话' })).toBeInTheDocument();
    expect(screen.getByRole('heading', { name: '智能体' })).toBeInTheDocument();
    expect(screen.getByText('可编辑的名册——点击智能体即可查看和编辑详情。')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '新建智能体' })).toBeInTheDocument();
    expect(screen.getByRole('tab', { name: '活跃' })).toBeInTheDocument();
    expect(screen.getByRole('tab', { name: '待审批' })).toBeInTheDocument();
    expect(screen.getByText('经理')).toBeInTheDocument();
    expect(screen.getByText('工作者')).toBeInTheDocument();
    for (const heading of ['执行器', '模型', '代码仓库', '问责指标', '最近任务', '清理活动', '经验']) {
      expect(screen.getByRole('heading', { name: heading })).toBeInTheDocument();
    }
    expect(screen.getByRole('button', { name: '添加代码仓库' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '移除 happyranch' })).toBeInTheDocument();
    expect(await screen.findByText('暂无经验')).toBeInTheDocument();
    expect(await screen.findByText('个任务')).toBeInTheDocument();
    expect(await screen.findByText('运行日期：2026年6月10日 · 任务：completed · 结果：blocked')).toBeInTheDocument();

    // Daemon values stay byte-verbatim.
    expect(screen.getAllByText('engineering_manager').length).toBeGreaterThan(0);
    expect(screen.getAllByText('Owns engineering delivery.').length).toBeGreaterThan(0);
    expect(screen.getByText('claude-sonnet-4-20250514')).toBeInTheDocument();
    expect(screen.getByText('manager')).toBeInTheDocument();
    expect(screen.getByText('TASK-CLEANUP-9')).toBeInTheDocument();
    expect(screen.getByText('Removed two stale worktrees.')).toBeInTheDocument();

    await act(async () => {
      fireEvent.click(screen.getByTestId('test-set-locale-en'));
    });
    expect(screen.getByRole('heading', { name: 'Agents' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Start Thread' })).toBeInTheDocument();
    expect(screen.getByText('tasks')).toBeInTheDocument();
  });

  test('the tasks unit is a plural catalog entry (en one/other, zh other)', () => {
    expect(Object.keys(en['agents.detail.tasksUnit'] as object).sort()).toEqual(['one', 'other']);
    expect(Object.keys(zhCN['agents.detail.tasksUnit'] as object)).toEqual(['other']);
    expect(translate('en', 'agents.detail.tasksUnit', { count: 1 })).toBe('task');
    expect(translate('en', 'agents.detail.tasksUnit', { count: 2 })).toBe('tasks');
    expect(translate('zh-CN', 'agents.detail.tasksUnit', { count: 1 })).toBe('个任务');
  });
});

describe('AddAgentDialog i18n', () => {
  test('zh-CN dialog chrome and the 关闭 close button', async () => {
    stub();
    mount('zh-CN', `/orgs/${SLUG}/agents/engineering_manager`);
    fireEvent.click(await screen.findByRole('button', { name: '新建智能体' }));
    const dialog = dialogOf(await screen.findByRole('heading', { name: '新建智能体' }));
    expect(within(dialog).getByRole('button', { name: '关闭' })).toBeInTheDocument();
    expect(within(dialog).getByText('名称')).toBeInTheDocument();
    expect(within(dialog).getByText('仅限小写字母、数字和下划线，且必须以字母开头。')).toBeInTheDocument();
    expect(within(dialog).getByText('角色')).toBeInTheDocument();
    expect(within(dialog).getByText('团队')).toBeInTheDocument();
    expect(within(dialog).getByRole('option', { name: '选择团队…' })).toBeInTheDocument();
    expect(within(dialog).getByRole('option', { name: 'engineering' })).toBeInTheDocument();
    expect(await within(dialog).findByRole('option', { name: '── 不可用 ──' })).toBeInTheDocument();
    expect(within(dialog).getByRole('option', { name: 'pi（未注册——设置 > 执行器）' })).toBeInTheDocument();
    expect(within(dialog).getByRole('button', { name: '创建' })).toBeInTheDocument();
    expect(within(dialog).getByRole('button', { name: '取消' })).toBeInTheDocument();
  });
});

describe('Pending enrollments i18n', () => {
  test('zh-CN rows, reject dialog with 关闭, daemon values verbatim', async () => {
    stub();
    mount('zh-CN', `/orgs/${SLUG}/agents?view=pending`);
    expect(await screen.findByText('new_writer')).toBeInTheDocument();
    expect(screen.getByText('团队：content · 执行器：claude · 登记人：content_manager')).toBeInTheDocument();
    expect(screen.getByText('Drafts long-form posts.')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '批准' })).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: '拒绝' }));
    const dialog = dialogOf(await screen.findByRole('heading', { name: '拒绝 new_writer？' }));
    expect(within(dialog).getByRole('button', { name: '关闭' })).toBeInTheDocument();
    expect(within(dialog).getByPlaceholderText('原因（可选）')).toBeInTheDocument();
  });

  test('load error: code-less detail verbatim, empty error falls back to localized copy', async () => {
    stub({ enrollmentsError: { status: 500, body: { detail: 'enrollment store offline' } } });
    const first = mount('zh-CN', `/orgs/${SLUG}/agents?view=pending`);
    expect(await screen.findByText('enrollment store offline')).toBeInTheDocument();
    first.unmount();

    stub({ enrollmentsError: { status: 500, body: {} } });
    mount('zh-CN', `/orgs/${SLUG}/agents?view=pending`);
    expect(await screen.findByText('无法加载待审批的登记。')).toBeInTheDocument();
  });
});

describe('Team escalation policy page i18n', () => {
  test('zh-CN chrome, confirm + discard dialogs with 关闭, policy bodies verbatim', async () => {
    stub();
    mountPolicy('zh-CN');
    const whatTo = await screen.findByRole('textbox', { name: '需要上报的情况' });
    expect(whatTo).toHaveValue('Escalate scope changes verbatim.');
    expect(screen.getByRole('textbox', { name: '无需上报的情况' })).toHaveValue('Continue ordinary work verbatim.');
    expect(screen.getByRole('heading', { level: 1, name: '团队上报策略' })).toBeInTheDocument();
    expect(screen.getByRole('link', { name: '← 返回 Engineering Manager' })).toBeInTheDocument();
    expect(screen.getByText('团队所有')).toBeInTheDocument();
    expect(screen.getByText('不可变的双文本历史')).toBeInTheDocument();
    expect(screen.getByText('team-8c85b6639e62e10b-dual-text')).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: '保存并激活' }));
    const confirm = dialogOf(await screen.findByRole('heading', { name: '保存并激活两段策略文本？' }));
    expect(within(confirm).getByRole('button', { name: '关闭' })).toBeInTheDocument();
    expect(within(confirm).getByRole('button', { name: '确认保存并激活' })).toBeInTheDocument();
    fireEvent.click(within(confirm).getByRole('button', { name: '取消' }));
    await waitFor(() => expect(screen.queryByRole('heading', { name: '保存并激活两段策略文本？' })).not.toBeInTheDocument());

    fireEvent.change(whatTo, { target: { value: 'Edited draft' } });
    fireEvent.click(screen.getByRole('link', { name: '← 返回 Engineering Manager' }));
    const discard = dialogOf(await screen.findByRole('heading', { name: '放弃未保存的策略更改？' }));
    expect(within(discard).getByRole('button', { name: '关闭' })).toBeInTheDocument();
    expect(within(discard).getByRole('button', { name: '留在此页' })).toBeInTheDocument();
    expect(within(discard).getByRole('button', { name: '放弃并继续' })).toBeInTheDocument();
  });
});

describe('classifyAgentError', () => {
  test('recognized code -> catalog key; unknown code raw; code-less detail/Error/string verbatim; empty -> fallback', () => {
    expect(classifyAgentError({ code: 'agent_exists' }, 'agents.error.createFailed')).toEqual({
      kind: 'message', key: 'agents.error.agentExists',
    });
    expect(
      renderAgentError(
        classifyAgentError({ code: 'agent_exists' }, 'agents.error.createFailed'),
        (key, params) => translate('zh-CN', key, params),
        { name: 'alpha_worker' },
      ),
    ).toBe('名为“alpha_worker”的智能体已存在。');
    expect(classifyAgentError({ code: 'odd_code' }, 'agents.error.createFailed')).toEqual({ kind: 'raw', text: 'odd_code' });
    expect(classifyAgentError({ code: null, detail: 'bad' }, 'agents.error.createFailed')).toEqual({ kind: 'raw', text: 'bad' });
    expect(classifyAgentError(new Error('boom'), 'agents.error.createFailed')).toEqual({ kind: 'raw', text: 'boom' });
    expect(classifyAgentError('thrown', 'agents.error.createFailed')).toEqual({ kind: 'raw', text: 'thrown' });
    expect(classifyAgentError({ code: '', detail: { code: '' } }, 'agents.error.createFailed')).toEqual({
      kind: 'message', key: 'agents.error.createFailed',
    });
    expect(classifyAgentError({ status: 500, code: null, detail: null, message: 'API 500' }, 'agents.error.createFailed')).toEqual({
      kind: 'message', key: 'agents.error.createFailed',
    });
    expect(
      renderAgentError(classifyAgentError(null, 'agents.error.createFailed'), (key, params) => translate('zh-CN', key, params)),
    ).toBe('无法创建智能体。');
  });
});
