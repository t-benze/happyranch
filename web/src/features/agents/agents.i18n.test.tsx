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
import { createMemoryRouter, MemoryRouter, RouterProvider } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest';
import { AppRoutes } from '@/routes';
import { AppProvider } from '@/design-system/providers/AppProvider';
import { I18nProvider } from '@/hooks/i18n';
import { LocaleTestSwitch, renderWithProviders, savedLocaleAdapter } from '@/test/render';
import { server } from '@/test/server';
import { translate } from '@/lib/i18n';
import { en, zhCN } from '@/lib/i18n/catalog';
import { formatDateShapeFor } from '@/lib/i18n/format';
import { classifyAgentError, renderAgentError } from './strings';

describe('THR296 human roster query states', () => {
  test.each(['en', 'zh-CN'] as const)('loading and failed roster recover in %s', async (locale) => {
    stub();
    let release!: () => void;
    const pending = new Promise<void>((resolve) => { release = resolve; });
    let failed = true;
    server.use(
      http.get(`${API}/orgs/${SLUG}/agents`, async () => {
        await pending;
        return failed ? HttpResponse.json({ detail: 'fixture outage' }, { status: 503 })
          : HttpResponse.json({ agents: [{ ...AGENTS.agents[1], name: 'consultant_codex', team: 'default' }] });
      }),
      http.get(`${API}/orgs/${SLUG}/teams`, () => HttpResponse.json({ teams: [{
        name: 'default', manager: null, manager_kind: 'human', human_manager: 'founder',
        is_default: true, workers: ['consultant_codex'],
      }] })),
      http.get(`${API}/orgs/${SLUG}/agents/:agent/cleanup-activity`, () => HttpResponse.json({ activities: [] })),
    );
    mount(locale, `/orgs/${SLUG}/agents`);
    try {
      expect(await screen.findByRole('status', { name: translate(locale, 'agents.common.loading') })).toBeInTheDocument();
    } finally {
      release();
    }
    const message = locale === 'en' ? 'Could not load agents.' : '无法加载智能体。';
    expect(await screen.findByText(message)).toBeInTheDocument();
    expect(screen.queryByText(translate(locale, 'agents.empty.title'))).not.toBeInTheDocument();
    failed = false;
    fireEvent.click(screen.getByRole('button', { name: translate(locale, 'common.retry') }));
    expect((await screen.findAllByText('consultant_codex')).length).toBeGreaterThan(0);
    expect(await screen.findByRole('textbox', { name: translate(locale, 'agents.detail.model') })).toBeInTheDocument();
    expect(screen.queryByText(message)).not.toBeInTheDocument();
    expect(screen.queryByTestId('team-escalation-policy')).not.toBeInTheDocument();
    expect(screen.queryByRole('link', { name: translate(locale, 'agents.policy.open') })).not.toBeInTheDocument();
  });

  test.each(['en', 'zh-CN'] as const)('empty human Default remains enrollable in %s', async (locale) => {
    stub();
    server.use(
      http.get(`${API}/orgs/${SLUG}/agents`, () => HttpResponse.json({ agents: [] })),
      http.get(`${API}/orgs/${SLUG}/teams`, () => HttpResponse.json({ teams: [{
        name: 'default', manager: null, manager_kind: 'human', human_manager: 'founder',
        is_default: true, workers: [],
      }] })),
    );
    mount(locale, `/orgs/${SLUG}/agents`);
    expect(await screen.findByText(translate(locale, 'agents.empty.title'))).toBeInTheDocument();
    expect(screen.queryByText('founder', { exact: true })).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: translate(locale, 'agents.empty.cta') }));
    const dialog = await screen.findByRole('dialog');
    expect(within(dialog).getByRole('combobox', { name: translate(locale, 'agents.add.team') })).toBeInTheDocument();
    expect(screen.queryByTestId('team-escalation-policy')).not.toBeInTheDocument();
  });
});

describe('THR296 human team query states', () => {
  test.each(['en', 'zh-CN'] as const)('team loading and failure preserve enrollment draft through retry in %s', async (locale) => {
    stub();
    let release!: () => void;
    const pending = new Promise<void>((resolve) => { release = resolve; });
    let failed = true;
    let teamReads = 0;
    const creates: unknown[] = [];
    server.use(
      http.get(`${API}/orgs/${SLUG}/agents`, () => HttpResponse.json({ agents: [] })),
      http.get(`${API}/orgs/${SLUG}/teams`, async () => {
        teamReads += 1;
        await pending;
        return failed ? HttpResponse.json({ detail: 'fixture outage' }, { status: 503 })
          : HttpResponse.json({ teams: [{ name: 'default', manager: null, manager_kind: 'human',
              human_manager: 'founder', is_default: true, workers: [] }] });
      }),
      http.post(`${API}/orgs/${SLUG}/agents`, async ({ request }) => {
        creates.push(await request.json());
        return HttpResponse.json({ name: 'new_consultant', team: 'default', role: 'worker' });
      }),
    );
    mount(locale, `/orgs/${SLUG}/agents`);
    fireEvent.click(await screen.findByRole('button', { name: translate(locale, 'agents.empty.cta') }));
    const dialog = await screen.findByRole('dialog');
    fireEvent.change(within(dialog).getByRole('textbox', { name: translate(locale, 'agents.add.name') }),
      { target: { value: 'new_consultant' } });
    fireEvent.change(within(dialog).getByRole('textbox', { name: translate(locale, 'agents.field.description') }),
      { target: { value: 'Original description' } });
    const prompt = within(dialog).getByRole('textbox', { name: translate(locale, 'agents.field.systemPrompt') });
    fireEvent.change(prompt, { target: { value: 'Original worker prompt' } });
    try {
      expect(within(dialog).getByRole('status')).toHaveTextContent(locale === 'en' ? 'Loading teams…' : '正在加载团队…');
      expect(within(dialog).queryByText(translate(locale, 'agents.add.noTeams'))).not.toBeInTheDocument();
      expect(within(dialog).getByRole('button', { name: translate(locale, 'agents.add.create') })).toBeDisabled();
    } finally {
      release();
    }
    const message = locale === 'en' ? 'Could not load teams.' : '无法加载团队。';
    expect(await within(dialog).findByRole('alert')).toHaveTextContent(message);
    expect(within(dialog).queryByText(translate(locale, 'agents.add.noTeams'))).not.toBeInTheDocument();
    expect(within(dialog).getByRole('button', { name: translate(locale, 'agents.add.create') })).toBeDisabled();
    failed = false;
    fireEvent.click(within(dialog).getByRole('button', { name: translate(locale, 'common.retry') }));
    const selector = await within(dialog).findByRole('combobox', { name: translate(locale, 'agents.add.team') });
    expect(within(selector).getByRole('option', { name: `default · ${translate(locale, 'agents.team.founderManaged')}` })).toBeInTheDocument();
    fireEvent.change(selector, { target: { value: 'default' } });
    await waitFor(() => expect(within(dialog).getByRole('combobox', { name: translate(locale, 'agents.executor.label') })).toHaveValue('claude'));
    prompt.focus();
    (prompt as HTMLTextAreaElement).setSelectionRange(1, 7);
    const next = locale === 'en' ? 'zh-CN' : 'en';
    fireEvent.click(screen.getByTestId(`test-set-locale-${next}`));
    expect(prompt).toHaveFocus();
    expect(prompt).toHaveValue('Original worker prompt');
    expect((prompt as HTMLTextAreaElement).selectionStart).toBe(1);
    expect((prompt as HTMLTextAreaElement).selectionEnd).toBe(7);
    expect(selector).toHaveValue('default');
    expect(within(dialog).getByRole('textbox', { name: translate(next, 'agents.add.name') })).toHaveValue('new_consultant');
    expect(within(dialog).getByRole('textbox', { name: translate(next, 'agents.field.description') })).toHaveValue('Original description');
    expect(teamReads).toBe(2);
    expect(creates).toEqual([]);
    fireEvent.click(within(dialog).getByRole('button', { name: translate(next, 'agents.add.create') }));
    await waitFor(() => expect(creates).toEqual([{ name: 'new_consultant', role: 'worker', team: 'default',
      executor: 'claude', description: 'Original description', system_prompt: 'Original worker prompt' }]));
  });

  test.each(['en', 'zh-CN'] as const)('removed selected human team cannot submit a stale draft in %s', async (locale) => {
    stub();
    let removed = false;
    const creates: unknown[] = [];
    server.use(
      http.get(`${API}/orgs/${SLUG}/agents`, () => HttpResponse.json({ agents: [] })),
      http.get(`${API}/orgs/${SLUG}/teams`, () => HttpResponse.json({ teams: removed
        ? [{ name: 'engineering', manager: 'engineering_manager', workers: [] }]
        : [{ name: 'default', manager: null, manager_kind: 'human', human_manager: 'founder',
            is_default: true, workers: [] }] })),
      http.post(`${API}/orgs/${SLUG}/agents`, async ({ request }) => {
        creates.push(await request.json());
        return HttpResponse.json({ name: 'new_consultant', team: 'default', role: 'worker' });
      }),
    );
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(<MemoryRouter initialEntries={[`/orgs/${SLUG}/agents`]}>
      <I18nProvider adapter={savedLocaleAdapter(locale)}>
        <AppProvider client={client}><AppRoutes /></AppProvider>
      </I18nProvider>
    </MemoryRouter>);
    fireEvent.click(await screen.findByRole('button', { name: translate(locale, 'agents.empty.cta') }));
    const dialog = await screen.findByRole('dialog');
    const team = await within(dialog).findByRole('combobox', { name: translate(locale, 'agents.add.team') });
    fireEvent.change(team, { target: { value: 'default' } });
    fireEvent.change(within(dialog).getByRole('textbox', { name: translate(locale, 'agents.add.name') }),
      { target: { value: 'new_consultant' } });
    fireEvent.change(within(dialog).getByRole('textbox', { name: translate(locale, 'agents.field.description') }),
      { target: { value: 'Original description' } });
    const prompt = within(dialog).getByRole('textbox', { name: translate(locale, 'agents.field.systemPrompt') });
    fireEvent.change(prompt, { target: { value: 'Original worker prompt' } });
    const create = within(dialog).getByRole('button', { name: translate(locale, 'agents.add.create') });
    await waitFor(() => expect(create).toBeEnabled());
    removed = true;
    await act(async () => { await client.invalidateQueries({ queryKey: ['teams', SLUG], exact: true }); });
    await waitFor(() => expect(within(team).getByRole('option', { name: 'engineering' })).toBeInTheDocument());
    expect(within(team).queryByRole('option', { name: /default/ })).not.toBeInTheDocument();
    expect(create).toBeDisabled();
    fireEvent.click(create);
    expect(creates).toEqual([]);
    expect(prompt).toHaveValue('Original worker prompt');
    fireEvent.change(team, { target: { value: 'engineering' } });
    expect(create).toBeEnabled();
  });
});

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
    http.get(`${API}/orgs/${SLUG}/agents/:agent/cleanup-activity`, () => HttpResponse.json({ activities: [] })),
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
        <AppProvider client={client}><AppRoutes /><LocaleTestSwitch to="en" /><LocaleTestSwitch to="zh-CN" /></AppProvider>
      </I18nProvider>
    ),
  }], { initialEntries: [`/orgs/${SLUG}/agents/engineering_manager/team-escalation-policy`] });
  return { ...render(<RouterProvider router={router} />), client, router };
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
  test.each(['en', 'zh-CN'] as const)('C8 %s mounted recent tasks preserve localized age, waiting and lineage', async (locale) => {
    stub();
    const now = new Date('2026-10-07T14:00:00Z').getTime();
    const clock = vi.spyOn(Date, 'now').mockReturnValue(now);
    const ages = [
      [29000, 'just now', '刚刚'], [30000, '1m', '1 分钟'],
      [3569000, '59m', '59 分钟'], [3570000, '1h', '1 小时'],
      [84569000, '23h', '23 小时'], [84570000, '1d', '1 天'],
    ] as const;
    server.use(http.get(`${API}/orgs/${SLUG}/tasks`, () => HttpResponse.json({
      tasks: ages.map(([elapsed], index) => ({
        task_id: `TASK-CASE-${index}`, status: 'in_progress', team: 'engineering',
        assigned_agent: 'engineering_manager', brief: `Raw task / 原文 ${index}`,
        block_kind: index === 0 ? 'delegated' : 'blocked_on_job',
        updated_at: new Date(now - elapsed).toISOString(),
        revisit_of_task_id: index === 0 ? 'TASK-CASE-P' : null,
        direct_revisits: index === 0 ? ['TASK-CASE-R'] : [],
      })),
    })));
    try {
      mount(locale, `/orgs/${SLUG}/agents/engineering_manager`);
      for (const [index, [, english, chinese]] of ages.entries()) {
        const link = await screen.findByRole('link', { name: new RegExp(`TASK-CASE-${index}`) });
        expect(link).toHaveAttribute('href', `/orgs/${SLUG}/tasks/TASK-CASE-${index}`);
        expect(link).toHaveTextContent(locale === 'en' ? english : chinese);
        expect(link).toHaveTextContent(index === 0
          ? locale === 'en' ? 'waiting on subtasks' : '等待子任务'
          : locale === 'en' ? 'waiting on jobs' : '等待作业');
        expect(link).toHaveTextContent('in_progress');
        expect(link).toHaveTextContent(`Raw task / 原文 ${index}`);
      }
      const previous = screen.getByRole('link', { name: locale === 'en' ? 'supersedes TASK-CASE-P' : '取代 TASK-CASE-P' });
      const revisit = screen.getByRole('link', { name: locale === 'en' ? 'superseded by TASK-CASE-R' : '已被 TASK-CASE-R 取代' });
      expect(previous).toHaveAttribute('href', `/orgs/${SLUG}/tasks/TASK-CASE-P`);
      expect(revisit).toHaveAttribute('href', `/orgs/${SLUG}/tasks/TASK-CASE-R`);
      expect(previous.closest('a a')).toBeNull();
    } finally { clock.mockRestore(); }
  });

  test('zh-CN chrome on the roster and detail pane, daemon values verbatim', async () => {
    stub();
    mount('zh-CN', `/orgs/${SLUG}/agents/engineering_manager`);

    expect(await screen.findByRole('button', { name: '发起会话' })).toBeInTheDocument();
    expect(screen.getByRole('heading', { name: '智能体' })).toBeInTheDocument();
    expect(screen.getByText('可编辑的名册——点击智能体即可查看和编辑详情。')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '新建智能体' })).toBeInTheDocument();
    expect(screen.getByRole('tab', { name: '活跃' })).toBeInTheDocument();
    expect(screen.getByRole('tab', { name: '待审批' })).toBeInTheDocument();
    // Roster role meta is the daemon role value, byte-verbatim (no translation).
    const roster = screen.getByText('support_agent').closest('aside')!;
    expect(within(roster).getByText('manager')).toBeInTheDocument();
    expect(within(roster).getByText('worker')).toBeInTheDocument();
    expect(within(roster).queryByText(zhCN['agents.role.manager'] as string)).not.toBeInTheDocument();
    expect(within(roster).queryByText(zhCN['agents.role.worker'] as string)).not.toBeInTheDocument();
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
    expect(screen.getAllByText('manager')).toHaveLength(2);
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
  test.each(['en', 'zh-CN'] as const)('%s roster roles and policy identifiers remain exact daemon bytes', async (locale) => {
    stub();
    const rosterView = mount(locale, `/orgs/${SLUG}/agents/engineering_manager`);
    const open = await screen.findByRole('link', { name: translate(locale, 'agents.policy.open') });
    const roster = screen.getByText('support_agent').closest('aside')!;
    expect(within(roster).getByText('manager', { exact: true }).textContent).toBe('manager');
    expect(within(roster).getByText('worker', { exact: true }).textContent).toBe('worker');
    expect(open.closest('[data-testid="team-escalation-policy"]')).toHaveTextContent(
      translate(locale, 'agents.policy.entryMeta', { team: 'engineering', name: 'engineering_manager' }),
    );
    rosterView.unmount();

    mountPolicy(locale);
    await screen.findByRole('textbox', { name: translate(locale, 'agents.policy.whatTo') });
    expect(screen.getByRole('link', { name: translate(locale, 'agents.policy.backTo', { name: 'engineering_manager' }) })).toBeInTheDocument();
    expect(screen.getByText('engineering · engineering_manager', { exact: true }).textContent).toBe('engineering · engineering_manager');
    expect(screen.getByText(translate(locale, 'agents.policy.ownedBy', { team: 'engineering' }), { exact: true })).toBeInTheDocument();
  });

  test('zh-CN chrome, confirm + discard dialogs with 关闭, policy bodies verbatim', async () => {
    stub();
    mountPolicy('zh-CN');
    const whatTo = await screen.findByRole('textbox', { name: '需要上报的情况' });
    expect(whatTo).toHaveValue('Escalate scope changes verbatim.');
    expect(screen.getByRole('textbox', { name: '无需上报的情况' })).toHaveValue('Continue ordinary work verbatim.');
    expect(screen.getByRole('heading', { level: 1, name: '团队上报策略' })).toBeInTheDocument();
    // Agent and team identifiers are byte-verbatim (no title-casing).
    expect(screen.getByRole('link', { name: '← 返回 engineering_manager' })).toBeInTheDocument();
    expect(screen.getByText('engineering · engineering_manager')).toBeInTheDocument();
    expect(screen.getByText('归 engineering 团队所有，而非该智能体。')).toBeInTheDocument();
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
    fireEvent.click(screen.getByRole('link', { name: '← 返回 engineering_manager' }));
    const discard = dialogOf(await screen.findByRole('heading', { name: '放弃未保存的策略更改？' }));
    expect(within(discard).getByRole('button', { name: '关闭' })).toBeInTheDocument();
    expect(within(discard).getByRole('button', { name: '留在此页' })).toBeInTheDocument();
    expect(within(discard).getByRole('button', { name: '放弃并继续' })).toBeInTheDocument();
  });
});

describe('Team escalation policy history dates', () => {
  // Noon UTC keeps the calendar day stable in every host timezone.
  const RELEASED = '2026-09-03T12:00:00Z';
  const ACTIVATED = '2026-09-04T12:30:00Z';

  function stubHistory() {
    server.use(
      http.get(`${API}/orgs/${SLUG}/agents/engineering_manager/team-escalation-policy/v2/history`, () =>
        HttpResponse.json({ items: [{
          family: 'v2', contract_version: 'v2', release_id: `APV2-${'d'.repeat(64)}`,
          policy_id: 'team-8c85b6639e62e10b-dual-text', version: 2, title: 'Dated release',
          what_to_escalate: 'Escalate dated.', what_not_to_escalate: 'Continue dated.',
          contract_digest: '4'.repeat(64), policy_digest: '5'.repeat(64),
          release_created_at: RELEASED, actor_attribution: 'shared local operator credential',
          activation: { id: `APV2A-${'e'.repeat(64)}`, selector_epoch: 4, action: 'activate',
            digest: '6'.repeat(64), created_at: ACTIVATED },
        }], next_cursor: null })),
    );
  }

  test.each(['en', 'zh-CN'] as const)('%s release and activation timestamps render through formatDateShapeFor', async (locale) => {
    stub();
    stubHistory();
    mountPolicy(locale);
    expect(await screen.findByText('Escalate dated.')).toBeInTheDocument();
    const released = formatDateShapeFor(locale, new Date(RELEASED), 'dateTime');
    const activated = formatDateShapeFor(locale, new Date(ACTIVATED), 'dateTime');
    expect(released).toContain(locale === 'en' ? 'Sep 3, 2026' : '2026年9月3日');
    expect(activated).toContain(locale === 'en' ? 'Sep 4, 2026' : '2026年9月4日');
    const text = document.body.textContent ?? '';
    expect(text).toContain(translate(locale, 'agents.policy.history.release', {
      release: `APV2-${'d'.repeat(64)}`, policyDigest: '5'.repeat(64), contract: '4'.repeat(64), created: released,
    }));
    expect(text).toContain(translate(locale, 'agents.policy.history.activation', {
      activation: `APV2A-${'e'.repeat(64)}`, epoch: 4, action: 'activate', digest: '6'.repeat(64), created: activated,
    }));
    expect(text).not.toContain(RELEASED);
    expect(text).not.toContain(ACTIVATED);
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


describe('THR296 human Default worker views', () => {
  test.each(['en', 'zh-CN'] as const)('%s keeps both ordinary consultants and denies manager controls', async (locale) => {
    stub();
    const agents = ['consultant_head', 'consultant_codex'].map((name) => ({
      name, team: 'default', role: 'worker', executor: name === 'consultant_head' ? 'claude' : 'codex',
      model: null, description: `Advice from ${name}`, repos: {}, system_prompt: 'Individual advice.',
    }));
    let policyReads = 0;
    const modelWrites: unknown[] = [];
    server.use(
      http.get(`${API}/orgs/${SLUG}/agents`, () => HttpResponse.json({ agents })),
      http.get(`${API}/orgs/${SLUG}/agents/:agent/cleanup-activity`, () =>
        HttpResponse.json({ activities: [] })),
      http.get(`${API}/orgs/${SLUG}/teams`, () => HttpResponse.json({ teams: [{
        name: 'default', manager: null, manager_kind: 'human', human_manager: 'founder',
        is_default: true, workers: agents.map((agent) => agent.name),
      }] })),
      http.get(`${API}/orgs/${SLUG}/agents/:agent/team-escalation-policy`, () => {
        policyReads += 1;
        return HttpResponse.json({ detail: 'ineligible' }, { status: 404 });
      }),
      http.put(`${API}/orgs/${SLUG}/agents/consultant_head/model`, async ({ request }) => {
        modelWrites.push(await request.json());
        return HttpResponse.json({ name: 'consultant_head', model: 'worker-draft' });
      }),
    );
    mount(locale, `/orgs/${SLUG}/agents/consultant_head`);
    expect((await screen.findAllByText('Advice from consultant_head')).length).toBeGreaterThan(0);
    expect(screen.getAllByText('consultant_codex').length).toBeGreaterThan(0);
    expect((await screen.findAllByText(translate(locale, 'agents.team.founderManaged'), { exact: false })).length).toBeGreaterThan(0);
    expect(screen.queryByTestId('team-escalation-policy')).not.toBeInTheDocument();
    expect(screen.queryByRole('link', { name: translate(locale, 'agents.policy.open') })).not.toBeInTheDocument();
    expect(policyReads).toBe(0);
    // Locale changes keep the existing worker editor, selection and original
    // action. The test switch invokes the real provider without stealing focus.
    const model = screen.getByRole('textbox', { name: translate(locale, 'agents.detail.model') });
    fireEvent.change(model, { target: { value: 'worker-draft' } });
    model.focus();
    (model as HTMLInputElement).setSelectionRange(2, 6);
    const next = locale === 'en' ? 'zh-CN' : 'en';
    fireEvent.click(screen.getByTestId(`test-set-locale-${next}`));
    expect(screen.getByRole('textbox', { name: translate(next, 'agents.detail.model') })).toBe(model);
    expect(model).toHaveValue('worker-draft');
    expect(model).toHaveFocus();
    expect((model as HTMLInputElement).selectionStart).toBe(2);
    expect((model as HTMLInputElement).selectionEnd).toBe(6);
    expect(modelWrites).toEqual([]);
    expect(policyReads).toBe(0);
    fireEvent.click(screen.getByRole('button', { name: translate(next, 'agents.detail.save') }));
    await waitFor(() => expect(modelWrites).toEqual([{ model: 'worker-draft' }]));
    await waitFor(() => expect(screen.queryByRole('button', { name: translate(next, 'agents.detail.save') })).not.toBeInTheDocument());
    fireEvent.click(screen.getByRole('button', { name: /consultant_codex.*worker/ }));
    expect((await screen.findAllByText('Advice from consultant_codex')).length).toBeGreaterThan(0);
    expect(screen.queryByTestId('team-escalation-policy')).not.toBeInTheDocument();
    expect(policyReads).toBe(0);
  });
});

// C10 projection boundary: coherent before/after HTTP roster data exercises
// the real cache and UI, not the offline utility or an executed migration.
describe('C10 coherent selected-consultant refresh', () => {
  test.each(['consultant_head', 'consultant_codex'] as const)('%s remains usable after demotion and locale/query refresh', async (selected) => {
    stub();
    let demoted = false;
    let rosterFailed = false;
    const writes: Array<{ target: string; body: unknown }> = [];
    const policyReads: string[] = [];
    const roster = () => [
      { ...AGENTS.agents[0] },
      { ...AGENTS.agents[0], name: 'product_head', team: 'product' },
      ...['consultant_head', 'consultant_codex'].map((name) => ({ ...AGENTS.agents[1], name,
        team: demoted ? 'default' : 'consultant', role: !demoted && name === 'consultant_head' ? 'manager' : 'worker' })),
    ];
    server.use(
      http.get(`${API}/orgs/${SLUG}/agents`, () => rosterFailed ? HttpResponse.json({}, { status: 503 }) : HttpResponse.json({ agents: roster() })),
      http.get(`${API}/orgs/${SLUG}/teams`, () => HttpResponse.json({ teams: [
        { name: 'engineering', manager: 'engineering_manager', workers: [] },
        { name: 'product', manager: 'product_head', workers: [] },
        demoted ? { name: 'default', manager: null, manager_kind: 'human', human_manager: 'founder',
          is_default: true, workers: ['consultant_head', 'consultant_codex'] }
          : { name: 'consultant', manager: 'consultant_head', workers: ['consultant_codex'] },
      ] })),
      http.get(`${API}/orgs/${SLUG}/agents/:agent/cleanup-activity`, () => HttpResponse.json({ activities: [] })),
      http.get(`${API}/orgs/${SLUG}/agents/:agent/team-escalation-policy`, ({ params }) => {
        policyReads.push(String(params.agent));
        return HttpResponse.json({ ...POLICY, team: 'consultant', target_manager: params.agent });
      }),
      http.put(`${API}/orgs/${SLUG}/agents/:agent/model`, async ({ params, request }) => {
        writes.push({ target: String(params.agent), body: await request.json() });
        return HttpResponse.json({ name: params.agent, model: 'Original C10 worker draft' });
      }),
    );
    const client = new QueryClient({ defaultOptions: { queries: { retry: false, refetchOnWindowFocus: false } } });
    render(<MemoryRouter initialEntries={[`/orgs/${SLUG}/agents/${selected}`]}>
      <I18nProvider adapter={savedLocaleAdapter('en')}><AppProvider client={client}>
        <AppRoutes /><LocaleTestSwitch to="zh-CN" />
      </AppProvider></I18nProvider>
    </MemoryRouter>);
    const model = await screen.findByRole('textbox', { name: 'Model' });
    if (selected === 'consultant_head') await screen.findByRole('link', { name: 'Open team escalation policy' });
    else expect(screen.queryByTestId('team-escalation-policy')).not.toBeInTheDocument();
    const readsBefore = policyReads.slice();
    fireEvent.change(model, { target: { value: 'Original C10 worker draft' } });
    model.focus();
    (model as HTMLInputElement).setSelectionRange(2, 8);
    expect(model).toHaveFocus();
    const localeRequests: string[] = [];
    const listener = ({ request }: { request: Request }) => { localeRequests.push(request.method + ' ' + new URL(request.url).pathname); };
    await waitFor(() => expect(client.isFetching()).toBe(0));
    server.events.on('request:start', listener);
    try {
      fireEvent.click(screen.getByTestId('test-set-locale-zh-CN'));
      await act(async () => { await new Promise((resolve) => setTimeout(resolve, 60)); });
      expect(screen.getByRole('textbox', { name: '模型' })).toBe(model);
      expect(model).toHaveFocus(); // External provider switch, not header selection.
    } finally { server.events.removeListener('request:start', listener); }
    expect(localeRequests).toEqual([]);
    demoted = true;
    await act(async () => { await Promise.all([
      client.invalidateQueries({ queryKey: ['agents', SLUG], exact: true }),
      client.invalidateQueries({ queryKey: ['teams', SLUG], exact: true }),
    ]); });
    await waitFor(() => expect(screen.queryByTestId('team-escalation-policy')).not.toBeInTheDocument());
    expect(screen.queryByRole('link', { name: '打开团队上报策略' })).not.toBeInTheDocument();
    expect(screen.getByRole('heading', { name: selected })).toBeInTheDocument();
    expect(screen.getByRole('textbox', { name: '模型' })).toBe(model);
    expect(model).toHaveValue('Original C10 worker draft');
    expect(model).toHaveFocus();
    expect((model as HTMLInputElement).selectionStart).toBe(2);
    expect((model as HTMLInputElement).selectionEnd).toBe(8);
    expect(screen.getByText(/由创始人管理/)).toBeInTheDocument();
    expect(policyReads).toEqual(readsBefore);
    for (const family of ['team-escalation-policy', 'team-escalation-policy-history', 'team-escalation-policy-v2-history', 'team-escalation-policy-outcomes']) {
      expect(client.getQueryData([family, SLUG, selected, 'consultant'])).toBeUndefined();
    }
    rosterFailed = true;
    await act(async () => { await client.invalidateQueries({ queryKey: ['agents', SLUG], exact: true }); });
    expect(await screen.findByText('无法加载智能体。')).toBeInTheDocument();
    expect(screen.getByRole('textbox', { name: '模型' })).toBe(model);
    expect(model).toHaveValue('Original C10 worker draft');
    expect(model).toHaveFocus();
    rosterFailed = false;
    fireEvent.click(screen.getByRole('button', { name: '重试' }));
    await waitFor(() => expect(screen.queryByText('无法加载智能体。')).not.toBeInTheDocument());
    expect(screen.getByRole('heading', { name: selected })).toBeInTheDocument();
    expect(model).toHaveValue('Original C10 worker draft');
    expect((model as HTMLInputElement).selectionStart).toBe(2);
    expect((model as HTMLInputElement).selectionEnd).toBe(8);
    expect(writes).toEqual([]);
    fireEvent.click(screen.getByRole('button', { name: '保存智能体' }));
    await waitFor(() => expect(writes).toEqual([{ target: selected, body: { model: 'Original C10 worker draft' } }]));
  });

  test.each(['en', 'zh-CN'] as const)('%s empty Default keeps populated Engineering/Product and never enrolls Founder', async (locale) => {
    stub();
    server.use(
      http.get(`${API}/orgs/${SLUG}/agents`, () => HttpResponse.json({ agents: [AGENTS.agents[0], {
        ...AGENTS.agents[0], name: 'product_head', team: 'product',
      }] })),
      http.get(`${API}/orgs/${SLUG}/teams`, () => HttpResponse.json({ teams: [
        { name: 'default', manager: null, manager_kind: 'human', human_manager: 'founder', is_default: true, workers: [] },
        { name: 'engineering', manager: 'engineering_manager', workers: [] },
        { name: 'product', manager: 'product_head', workers: [] },
      ] })),
    );
    mount(locale, `/orgs/${SLUG}/agents/engineering_manager`);
    await screen.findByRole('link', { name: translate(locale, 'agents.policy.open') });
    expect(screen.getByRole('button', { name: /product_head.*manager/ })).toBeInTheDocument();
    expect(screen.queryByText(translate(locale, 'agents.empty.title'))).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: translate(locale, 'agents.page.newAgent') }));
    const dialog = await screen.findByRole('dialog');
    const team = within(dialog).getByRole('combobox', { name: translate(locale, 'agents.add.team') });
    expect(within(team).getByRole('option', { name: `default · ${translate(locale, 'agents.team.founderManaged')}` })).toHaveValue('default');
    expect(within(team).getByRole('option', { name: 'engineering' })).toBeInTheDocument();
    expect(within(team).getByRole('option', { name: 'product' })).toBeInTheDocument();
    expect(within(team).queryByRole('option', { name: 'consultant' })).not.toBeInTheDocument();
    expect(within(team).queryByRole('option', { name: 'founder' })).not.toBeInTheDocument();
  });
});

// Current v2 payload, real eligibility/editor/provider/confirmation/blocker.
// MSW supplies only the transport projection and paired-control response.
describe('C10 eligible Engineering draft and original action', () => {
  test.each(['en', 'zh-CN'] as const)('%s preserves exact policy draft across locale/error/retry/Stay and discards only explicitly', async (locale) => {
    stub();
    let failed = false;
    let projection: Record<string, unknown> = POLICY;
    const writes: Array<Record<string, unknown>> = [];
    const release = `APV2-${'c'.repeat(64)}`;
    const activation = `APV2A-${'d'.repeat(64)}`;
    const selector = `APS-${'e'.repeat(64)}`;
    server.use(
      http.get(`${API}/orgs/${SLUG}/agents/engineering_manager/team-escalation-policy`, () =>
        failed ? HttpResponse.json({}, { status: 503 }) : HttpResponse.json(projection)),
      http.post(`${API}/orgs/${SLUG}/agents/engineering_manager/team-escalation-policy/v2/releases`, async ({ request }) => {
        const body = await request.json() as Record<string, unknown>;
        writes.push(body);
        projection = { ...POLICY, family: 'v2', contract_version: 'v2', selector_id: selector, selector_epoch: 1,
          active: { family: 'v2', activation_id: activation, selector_epoch: 1, action: 'bootstrap',
            created_at: '2026-10-10T00:00:00Z', actor_attribution: 'shared local operator credential',
            release: { id: release, policy_id: POLICY.v2_starter.policy_id, version: 1,
              title: POLICY.v2_starter.title, what_to_escalate: 'C10 exact escalation / 上报',
              what_not_to_escalate: 'C10 exact continuation / 继续', digest: 'c'.repeat(64),
              actor_attribution: 'shared local operator credential' } } };
        return HttpResponse.json({ control: 'v2_create_activate', family: 'v2', contract_version: 'v2',
          selector_id: selector, selector_epoch: 1, previous_selector_id: POLICY.selector_id,
          receipt: { kind: 'v2_create_activate', team: 'engineering', create_request_id: body.create_request_id,
            create_request_digest: '1'.repeat(64), activation_request_digest: '2'.repeat(64),
            activation_digest: 'd'.repeat(64), created_at: '2026-10-10T00:00:00Z',
            activation_request_id: body.activation_request_id, action: 'bootstrap', selector_id: selector,
            selector_epoch: 1, previous_selector_id: POLICY.selector_id, release_id: release,
            release_version: 1, policy_digest: 'c'.repeat(64), activation_id: activation } }, { status: 201 });
      }),
    );
    const { client, router } = mountPolicy(locale);
    const to = await screen.findByRole('textbox', { name: translate(locale, 'agents.policy.whatTo') });
    const not = screen.getByRole('textbox', { name: translate(locale, 'agents.policy.whatNot') });
    fireEvent.change(to, { target: { value: 'C10 exact escalation / 上报' } });
    fireEvent.change(not, { target: { value: 'C10 exact continuation / 继续' } });
    to.focus(); (to as HTMLTextAreaElement).setSelectionRange(2, 8);
    await waitFor(() => expect(client.isFetching()).toBe(0));
    const requests: string[] = [];
    const listener = ({ request }: { request: Request }) => { requests.push(request.method + ' ' + new URL(request.url).pathname); };
    const next = locale === 'en' ? 'zh-CN' : 'en';
    server.events.on('request:start', listener);
    try {
      fireEvent.click(screen.getByTestId(`test-set-locale-${next}`));
      await act(async () => { await new Promise((resolve) => setTimeout(resolve, 60)); });
      expect(screen.getByRole('textbox', { name: translate(next, 'agents.policy.whatTo') })).toBe(to);
      expect(to).toHaveFocus();
      expect((to as HTMLTextAreaElement).selectionStart).toBe(2);
      expect((to as HTMLTextAreaElement).selectionEnd).toBe(8);
    } finally { server.events.removeListener('request:start', listener); }
    expect(requests).toEqual([]);
    failed = true;
    await act(async () => { await client.invalidateQueries({ queryKey: ['team-escalation-policy', SLUG, 'engineering_manager', 'engineering'], exact: true }); });
    expect(await screen.findByRole('alert')).toHaveTextContent(translate(next, 'agents.policy.loadError'));
    failed = false;
    fireEvent.click(screen.getByRole('button', { name: translate(next, 'common.retry') }));
    const recovered = await screen.findByRole('textbox', { name: translate(next, 'agents.policy.whatTo') });
    expect(recovered).toHaveValue('C10 exact escalation / 上报');
    expect(screen.getByRole('textbox', { name: translate(next, 'agents.policy.whatNot') })).toHaveValue('C10 exact continuation / 继续');
    // The error replaced the editor DOM, so focus is legitimately transferred.
    recovered.focus(); expect(recovered).toHaveFocus();
    fireEvent.click(screen.getByRole('link', { name: translate(next, 'agents.policy.backTo', { name: 'engineering_manager' }) }));
    const discard = dialogOf(await screen.findByRole('heading', { name: translate(next, 'agents.policy.discardTitle') }));
    fireEvent.click(within(discard).getByRole('button', { name: translate(next, 'agents.policy.stay') }));
    await waitFor(() => expect(discard).not.toBeInTheDocument());
    expect(recovered).toHaveValue('C10 exact escalation / 上报');
    fireEvent.click(screen.getByRole('button', { name: translate(next, 'agents.policy.save') }));
    const confirm = dialogOf(await screen.findByRole('heading', { name: translate(next, 'agents.policy.confirmTitle') }));
    expect(writes).toEqual([]);
    fireEvent.click(within(confirm).getByRole('button', { name: translate(next, 'agents.policy.confirmSave') }));
    await waitFor(() => expect(writes).toHaveLength(1));
    expect(writes[0]).toEqual({ team: 'engineering', policy_id: POLICY.v2_starter.policy_id,
      title: POLICY.v2_starter.title, action: 'bootstrap', based_on_selector_id: null,
      expected_selector_id: null, acknowledge_shared_credential_attribution: true,
      create_request_id: expect.any(String), activation_request_id: expect.any(String),
      what_to_escalate: 'C10 exact escalation / 上报', what_not_to_escalate: 'C10 exact continuation / 继续' });
    await waitFor(() => expect(screen.getByTestId('team-escalation-policy')).toHaveTextContent(translate(next, 'agents.policy.msg.saved', { release, activation, selector, digest: 'c'.repeat(64) })));
    expect(screen.getByRole('button', { name: translate(next, 'agents.policy.save') })).toBeDisabled();
    // A second, unsaved draft must be discarded by the shipping blocker.
    fireEvent.change(screen.getByRole('textbox', { name: translate(next, 'agents.policy.whatTo') }), { target: { value: 'Explicitly discarded draft' } });
    fireEvent.click(screen.getByRole('link', { name: translate(next, 'agents.policy.backTo', { name: 'engineering_manager' }) }));
    const discardAgain = dialogOf(await screen.findByRole('heading', { name: translate(next, 'agents.policy.discardTitle') }));
    fireEvent.click(within(discardAgain).getByRole('button', { name: translate(next, 'agents.policy.discard') }));
    await waitFor(() => expect(router.state.location.pathname).toBe(`/orgs/${SLUG}/agents/engineering_manager`));
    expect(writes).toHaveLength(1);
  });
});

// Existing worker selector: real compose/filter/state, transport-only MSW.
describe('C10 consultant recipient selector', () => {
  test.each([
    ['en', 'consultant_head'], ['en', 'consultant_codex'],
    ['zh-CN', 'consultant_head'], ['zh-CN', 'consultant_codex'],
  ] as const)('%s retains %s, filter and compose draft through locale/query refresh and sends once', async (locale, selected) => {
    stub();
    const agents = ['consultant_head', 'consultant_codex'].map(name => ({ ...AGENTS.agents[1], name, team: 'default', role: 'worker' }));
    const writes: unknown[] = [];
    let rosterReads = 0;
    server.use(
      http.get(`${API}/orgs/${SLUG}/agents`, () => { rosterReads++; return HttpResponse.json({ agents }); }),
      http.get(`${API}/orgs/${SLUG}/teams`, () => HttpResponse.json({ teams: [{ name: 'default',
        manager: null, manager_kind: 'human', human_manager: 'founder', is_default: true,
        workers: ['consultant_head', 'consultant_codex'] }] })),
      http.post(`${API}/orgs/${SLUG}/threads`, async ({ request }) => {
        writes.push(await request.json());
        return HttpResponse.json({ thread_id: 'THR-C10', started_at: 'now', pending_replies: 1 }, { status: 201 });
      }),
      http.get(`${API}/orgs/${SLUG}/threads/THR-C10`, () => HttpResponse.json({ thread_id: 'THR-C10',
        subject: 'C10 exact subject', status: 'open', started_at: 'now', archived_at: null,
        forwarded_from_id: null, forwarded_from_kind: null, turn_cap: 500, turns_used: 0,
        summary: null, transcript_path: null, participants: [selected], messages: [], reply_delivery: [] })),
      http.get(`${API}/orgs/${SLUG}/threads`, () => HttpResponse.json({ threads: [] })),
      http.get(`${API}/orgs/${SLUG}/threads/events`, () => HttpResponse.text('', { headers: { 'content-type': 'text/event-stream' } })),
      http.get(`${API}/orgs/${SLUG}/threads/THR-C10/messages`, () => HttpResponse.json({ messages: [] })),
      http.get(`${API}/orgs/${SLUG}/threads/THR-C10/tail`, () => HttpResponse.text('', { headers: { 'content-type': 'text/event-stream' } })),
    );
    const client = new QueryClient({ defaultOptions: { queries: { retry: false, refetchOnWindowFocus: false } } });
    render(<MemoryRouter initialEntries={[`/orgs/${SLUG}/agents/${selected}`]}>
      <I18nProvider adapter={savedLocaleAdapter(locale)}><AppProvider client={client}>
        <AppRoutes /><LocaleTestSwitch to="en" /><LocaleTestSwitch to="zh-CN" />
      </AppProvider></I18nProvider>
    </MemoryRouter>);
    fireEvent.click(await screen.findByRole('button', { name: translate(locale, 'agents.detail.startThread') }));
    const dialog = await screen.findByRole('dialog');
    const recipients = within(dialog).getByRole('textbox', { name: translate(locale, 'threads.newThread.recipientsLabel') });
    expect(recipients).toHaveValue(selected);
    fireEvent.change(within(dialog).getByRole('textbox', { name: translate(locale, 'threads.newThread.subjectLabel') }), { target: { value: 'C10 exact subject' } });
    fireEvent.change(within(dialog).getByRole('textbox', { name: translate(locale, 'threads.newThread.bodyLabel') }), { target: { value: 'C10 exact body / 原文' } });
    await act(async () => {
      fireEvent.change(recipients, { target: { value: 'consultant_' } });
      recipients.focus(); (recipients as HTMLInputElement).setSelectionRange(11, 11);
      fireEvent.keyUp(recipients, { key: '_' });
    });
    expect(await screen.findByRole('option', { name: /consultant_head default/ })).toBeInTheDocument();
    expect(screen.getByRole('option', { name: /consultant_codex default/ })).toBeInTheDocument();
    expect(screen.queryByRole('option', { name: /founder|consultant .*manager/ })).not.toBeInTheDocument();
    await waitFor(() => expect(client.isFetching()).toBe(0));
    const next = locale === 'en' ? 'zh-CN' : 'en';
    const requests: string[] = [];
    const listener = ({ request }: { request: Request }) => requests.push(request.method + ' ' + new URL(request.url).pathname);
    server.events.on('request:start', listener);
    try {
      // External provider change: an open modal's focus trap excludes the header.
      fireEvent.click(screen.getByTestId(`test-set-locale-${next}`));
      await act(async () => { await new Promise(resolve => setTimeout(resolve, 60)); });
    } finally { server.events.removeListener('request:start', listener); }
    expect(requests).toEqual([]);
    const readsBefore = rosterReads;
    await act(async () => { await client.invalidateQueries({ queryKey: ['agents', SLUG], exact: true }); });
    await waitFor(() => expect(rosterReads).toBe(readsBefore + 1));
    expect(within(dialog).getByRole('textbox', { name: translate(next, 'threads.newThread.recipientsLabel') })).toBe(recipients);
    expect(recipients).toHaveValue('consultant_'); expect(recipients).toHaveFocus();
    expect((recipients as HTMLInputElement).selectionStart).toBe(11);
    expect((recipients as HTMLInputElement).selectionEnd).toBe(11);
    expect(within(dialog).getByRole('textbox', { name: translate(next, 'threads.newThread.subjectLabel') })).toHaveValue('C10 exact subject');
    expect(within(dialog).getByRole('textbox', { name: translate(next, 'threads.newThread.bodyLabel') })).toHaveValue('C10 exact body / 原文');
    await act(async () => { fireEvent.mouseDown(screen.getByRole('option', { name: new RegExp(`${selected} default`) })); });
    await waitFor(() => expect(recipients).toHaveValue(`${selected}, `));
    expect(writes).toEqual([]);
    fireEvent.click(within(dialog).getByRole('button', { name: translate(next, 'threads.newThread.send') }));
    await waitFor(() => expect(writes).toEqual([{ subject: 'C10 exact subject', recipients: [selected], body_markdown: 'C10 exact body / 原文' }]));
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument());
  });
});
