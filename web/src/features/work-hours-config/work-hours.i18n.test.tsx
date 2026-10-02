/**
 * THR-118 W4b — Work Hours route family (`work-hours`, `work-hours/:agent`,
 * the `?view=wakes` tab, TierEditorDialog and the shared
 * EligibilityEditorDialog) i18n.
 *
 * Renders the real routed pages (and the two dialogs) under the real
 * I18nProvider and asserts:
 *  - zh-CN product copy for the overview roster, agent detail, wakes list and
 *    both editors, in the populated / empty / error states;
 *  - daemon/config values (agent and team names, leaf keys, mode values,
 *    intervals, timezones, routine-task bullets, wake summaries/errors, the
 *    next-wakes error and 422 messages) stay byte-identical;
 *  - a locale switch keeps the same nodes, a typed draft and focus, and issues
 *    zero requests;
 *  - the load-error diagnostic boundary: a code-less string detail and an
 *    unknown code render verbatim; an empty code / non-string detail falls back
 *    to localized copy — never the synthetic `API <status>` message.
 */
import { act, fireEvent, screen, within } from '@testing-library/react';
import { http, HttpResponse } from 'msw';
import { Route, Routes } from 'react-router-dom';
import { beforeEach, describe, expect, test } from 'vitest';
import { AppRoutes } from '@/routes';
import { EligibilityEditorDialog } from '@/shared/work-hours/EligibilityEditorDialog';
import { LocaleTestSwitch, renderWithProviders, savedLocaleAdapter } from '@/test/render';
import { server } from '@/test/server';
import type {
  AgentSummary,
  NextWakesResponse,
  WorkHourRecord,
  WorkingHoursSettings,
} from '@/lib/api/types';
import { TierEditorDialog } from './TierEditorDialog';

const SLUG = 'alpha';

function wh(overrides: Partial<WorkingHoursSettings> = {}): WorkingHoursSettings {
  return {
    enabled: true,
    agents: { mode: 'all', include: [], exclude: ['support_bot'] },
    default: {
      mode: 'windowed',
      window: { start: '09:00', end: '17:00', timezone: 'UTC' },
      interval: '2h',
      days: ['mon', 'tue', 'wed', 'thu', 'fri'],
      catch_up_on_startup: false,
    },
    teams: {
      eng: {
        mode: null,
        window: { start: null, end: null, timezone: 'America/Los_Angeles' },
        interval: null,
        days: null,
        catch_up_on_startup: null,
      },
    },
    overrides: {
      dev_agent: {
        mode: null,
        window: { start: null, end: '19:00', timezone: null },
        interval: '30m',
        days: null,
        catch_up_on_startup: null,
      },
    },
    ...overrides,
  };
}

function agent(name: string, systemPrompt: string): AgentSummary {
  return {
    name,
    team: 'eng',
    role: 'worker',
    executor: 'claude',
    description: null,
    repos: {},
    system_prompt: systemPrompt,
  };
}

const AGENTS = [
  agent('dev_agent', '## Routine Tasks\n- Review open PRs\n- Triage bugs'),
  agent('support_bot', 'No routine section here.'),
];

function wake(over: Partial<WorkHourRecord>): WorkHourRecord {
  return {
    work_hour_id: 'WH-1',
    agent_name: 'dev_agent',
    local_date: '2026-06-18',
    slot: '09:00',
    mode: 'windowed',
    scheduled_for: '2026-06-18T09:00:00Z',
    started_at: null,
    ended_at: null,
    status: 'completed',
    routine_count: 2,
    spawned_task_ids: ['TASK-77'],
    spawned_task_count: 1,
    summary: 'Reviewed 3 PRs.',
    transcript_path: null,
    session_id: null,
    error: null,
    created_at: '2026-06-18T09:00:00Z',
    ...over,
  };
}

const WAKES = [
  wake({}),
  wake({
    work_hour_id: 'WH-2',
    slot: '11:00',
    scheduled_for: '2026-06-18T11:00:00Z',
    status: 'failed',
    routine_count: 1,
    spawned_task_ids: [],
    summary: null,
    error: 'Executor exited 137',
  }),
  wake({
    work_hour_id: 'WH-3',
    agent_name: 'qa_engineer',
    status: 'weird_state',
    routine_count: 0,
    spawned_task_ids: [],
    summary: null,
  }),
];

const NEXT: NextWakesResponse = {
  agent: 'dev_agent',
  enabled: true,
  timezone: 'America/Los_Angeles',
  mode: 'windowed',
  next_wakes: ['2026-06-27T15:00:00-07:00'],
  error: null,
};

type SettingsMode =
  | { kind: 'ok'; wh?: WorkingHoursSettings }
  | { kind: 'error'; body: Record<string, unknown>; status?: number };

function settingsBody(w: WorkingHoursSettings) {
  return {
    system: {
      claude_cli_path: { value: '/c', restart_required: true },
      codex_cli_path: { value: '/c', restart_required: true },
      opencode_cli_path: { value: '/c', restart_required: true },
      pi_cli_path: { value: '/c', restart_required: true },
      session_timeout_seconds: { value: 1800, restart_required: false },
      queue_workers: { value: 3, restart_required: true },
      host_global_session_cap: { value: 13, restart_required: true },
      protocol_dir: { value: 'protocol', restart_required: true },
    },
    org: {
      session_timeout_seconds: null,
      dreaming: {
        enabled: true,
        schedule: { time: '02:00', timezone: 'UTC' },
        catch_up_on_startup: false,
        agents: { mode: 'all', include: [], exclude: [] },
      },
      threads: { enabled: true, default_turn_cap: 500, invocation_timeout_seconds: null },
      working_hours: w,
    },
  };
}

function stub(
  opts: {
    settings?: SettingsMode;
    agents?: AgentSummary[];
    next?: NextWakesResponse;
    wakes?: WorkHourRecord[];
    wakesError?: Record<string, unknown>;
  } = {},
) {
  const settings = opts.settings ?? { kind: 'ok' };
  server.use(
    http.get('/api/v1/orgs', () => HttpResponse.json({ orgs: [{ slug: SLUG, root: '/x' }] })),
    http.get(`/api/v1/orgs/${SLUG}/settings`, () =>
      settings.kind === 'ok'
        ? HttpResponse.json(settingsBody(settings.wh ?? wh()))
        : HttpResponse.json(settings.body, { status: settings.status ?? 500 }),
    ),
    http.get(`/api/v1/orgs/${SLUG}/agents`, () =>
      HttpResponse.json({ agents: opts.agents ?? AGENTS }),
    ),
    http.get(`/api/v1/orgs/${SLUG}/teams`, () =>
      HttpResponse.json({
        teams: [{ name: 'eng', manager: 'lead', workers: ['dev_agent', 'support_bot'] }],
      }),
    ),
    http.get(`/api/v1/orgs/${SLUG}/work-hours/next-wakes`, () =>
      HttpResponse.json(opts.next ?? NEXT),
    ),
    http.get(`/api/v1/orgs/${SLUG}/work-hours`, () =>
      opts.wakesError !== undefined
        ? HttpResponse.json(opts.wakesError, { status: 500 })
        : HttpResponse.json({ work_hours: opts.wakes ?? WAKES }),
    ),
  );
}

const SWITCHES = (
  <>
    <LocaleTestSwitch to="zh-CN" />
    <LocaleTestSwitch to="en" />
  </>
);

function mount(route: string, locale: 'en' | 'zh-CN') {
  return renderWithProviders(
    <>
      <AppRoutes />
      {SWITCHES}
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

beforeEach(() => {
  sessionStorage.setItem('happyranch.token', 'tok');
  localStorage.clear();
});

describe('Work Hours overview i18n', () => {
  test('zh-CN roster chrome, verbatim config values, same link node and zero requests across switches', async () => {
    stub();
    mount(`/orgs/${SLUG}/work-hours`, 'zh-CN');

    expect(await screen.findByText('dev_agent')).toBeInTheDocument();
    expect(screen.getByText('工时 · 仅限创始人配置')).toBeInTheDocument();
    expect(screen.getByRole('heading', { name: '组织何时在线。' })).toBeInTheDocument();
    expect(screen.getByRole('link', { name: '查看唤醒历史' })).toBeInTheDocument();
    expect(screen.getByRole('tab', { name: '概览' })).toBeInTheDocument();
    expect(screen.getByRole('tab', { name: '唤醒' })).toBeInTheDocument();
    expect(screen.getByText('开')).toBeInTheDocument();
    expect(screen.getByRole('link', { name: '管理运行控制' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '编辑组织默认值' })).toBeInTheDocument();
    expect(screen.getByText('编辑团队…')).toBeInTheDocument();
    for (const h of ['智能体', '团队', '模式', '节奏（生效）', '开启', '资格']) {
      expect(screen.getByRole('columnheader', { name: h })).toBeInTheDocument();
    }
    expect(screen.getByText('符合资格')).toBeInTheDocument();
    expect(screen.getByText('已排除')).toBeInTheDocument();
    expect(screen.getByText('关闭')).toBeInTheDocument();
    // Cadence words localized; interval/window/days/timezone values verbatim.
    expect(screen.getByText('每 30m · 09:00–19:00 mon,tue,wed,thu,fri America/Los_Angeles')).toBeInTheDocument();
    // Config + daemon values verbatim.
    expect(screen.getAllByText('windowed').length).toBe(2);
    expect(screen.getAllByText('eng').length).toBe(2);
    expect(screen.getByText('support_bot')).toBeInTheDocument();

    const link = screen.getByRole('link', { name: 'dev_agent' });
    const requests = await countRequests(async () => {
      await switchLocale('en');
      expect(screen.getByText('Working hours · Founder-only configuration')).toBeInTheDocument();
      expect(screen.getByText('every 30m · 09:00–19:00 mon,tue,wed,thu,fri America/Los_Angeles')).toBeInTheDocument();
      expect(screen.getByRole('columnheader', { name: 'Cadence (effective)' })).toBeInTheDocument();
      expect(screen.getByText('Excluded')).toBeInTheDocument();
      expect(screen.getByRole('link', { name: 'dev_agent' })).toBe(link);
      await switchLocale('zh-CN');
      expect(screen.getByRole('link', { name: 'dev_agent' })).toBe(link);
      expect(screen.getByText('已排除')).toBeInTheDocument();
    });
    expect(requests).toEqual([]);
  });

  test('empty roster, no-routine flag and inherited cadence are localized', async () => {
    stub({ agents: [] });
    const { unmount } = mount(`/orgs/${SLUG}/work-hours`, 'zh-CN');
    expect(await screen.findByText('暂无智能体')).toBeInTheDocument();
    expect(screen.getByText('该组织还没有智能体。登记智能体后即可配置工时。')).toBeInTheDocument();
    unmount();

    stub({
      agents: [agent('ops_bot', 'nothing')],
      settings: {
        kind: 'ok',
        wh: wh({
          agents: { mode: 'all', include: [], exclude: [] },
          default: {
            mode: null,
            window: { start: null, end: null, timezone: null },
            interval: null,
            days: null,
            catch_up_on_startup: null,
          },
          teams: {},
          overrides: {},
        }),
      },
    });
    mount(`/orgs/${SLUG}/work-hours`, 'zh-CN');
    expect(await screen.findByText('（继承组织默认值）')).toBeInTheDocument();
    expect(screen.getByText('无例行任务')).toBeInTheDocument();
  });

  test('recovery banner: code-less string detail stays verbatim, never the synthetic API status', async () => {
    stub({ settings: { kind: 'error', body: { detail: 'OrgConfigError: bad block' } } });
    mount(`/orgs/${SLUG}/work-hours`, 'zh-CN');
    expect(await screen.findByText('实时配置加载失败，调度已降级。')).toBeInTheDocument();
    expect(screen.getByText('OrgConfigError: bad block')).toBeInTheDocument();
    expect(screen.getByText('修复工时配置并保存即可恢复。')).toBeInTheDocument();
    expect(screen.queryByText(/API 500/)).toBeNull();
  });

  test('recovery banner: unknown code raw; empty code falls back to localized copy and re-translates', async () => {
    stub({ settings: { kind: 'error', body: { detail: { code: 'org_config_locked' } } } });
    const { unmount } = mount(`/orgs/${SLUG}/work-hours`, 'zh-CN');
    expect(await screen.findByText('org_config_locked')).toBeInTheDocument();
    unmount();

    stub({ settings: { kind: 'error', body: { detail: { code: '' } } } });
    mount(`/orgs/${SLUG}/work-hours`, 'zh-CN');
    expect(await screen.findByText('无法读取工时配置。')).toBeInTheDocument();
    expect(screen.queryByText(/API 500/)).toBeNull();
    await switchLocale('en');
    expect(screen.getByText('The work-hours config could not be read.')).toBeInTheDocument();
  });
});

describe('Work Hours agent detail i18n', () => {
  test('zh-CN detail chrome with verbatim leaf keys, values, routine bullets and next-wakes timezone', async () => {
    stub();
    mount(`/orgs/${SLUG}/work-hours/dev_agent`, 'zh-CN');

    expect(await screen.findByText('生效计划 — 来源')).toBeInTheDocument();
    expect(screen.getByRole('link', { name: '← 工时' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '编辑团队：eng' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '编辑本智能体' })).toBeInTheDocument();
    expect(screen.getByRole('columnheader', { name: '配置项' })).toBeInTheDocument();
    expect(screen.getByRole('columnheader', { name: '团队：eng' })).toBeInTheDocument();
    expect(screen.getByRole('columnheader', { name: '生效值' })).toBeInTheDocument();
    expect(screen.getAllByText('本智能体').length).toBeGreaterThanOrEqual(2);
    expect(screen.getAllByText('组织默认').length).toBeGreaterThanOrEqual(2);
    expect(screen.getAllByText('团队：eng').length).toBeGreaterThanOrEqual(2);
    // Leaf keys and values verbatim.
    expect(screen.getByText('window.timezone')).toBeInTheDocument();
    expect(screen.getByText('▶ 30m')).toBeInTheDocument();
    expect(screen.getByText('▶ America/Los_Angeles')).toBeInTheDocument();

    expect(screen.getByText('后续唤醒')).toBeInTheDocument();
    expect(await screen.findByText(/^每次唤醒派发：/)).toHaveTextContent(
      '每次唤醒派发：Review open PRs; Triage bugs',
    );
    expect(screen.getByText(/\(America\/Los_Angeles\)/)).toBeInTheDocument();
    expect(screen.getByText('例行任务')).toBeInTheDocument();
    expect(screen.getByText('（只读 · 编辑将在第二阶段提供）')).toBeInTheDocument();
    expect(screen.getByText('Review open PRs')).toBeInTheDocument();
    const hint = screen.getByText(/在 MVP 中如需修改/);
    expect(hint).toHaveTextContent('在 MVP 中如需修改，请直接编辑该智能体的 ## Routine Tasks markdown。');
    expect(within(hint).getByText('## Routine Tasks').tagName).toBe('CODE');

    const heading = screen.getByRole('heading', { name: 'dev_agent' });
    const requests = await countRequests(async () => {
      await switchLocale('en');
      expect(screen.getByText('Effective schedule — provenance')).toBeInTheDocument();
      expect(screen.getByText(/^To change these in MVP/)).toHaveTextContent(
        'To change these in MVP, edit the agent’s ## Routine Tasks markdown directly.',
      );
      expect(screen.getByRole('heading', { name: 'dev_agent' })).toBe(heading);
      await switchLocale('zh-CN');
      expect(screen.getByRole('heading', { name: 'dev_agent' })).toBe(heading);
    });
    expect(requests).toEqual([]);
  });

  test('next-wakes error stays verbatim inside localized copy; excluded + no-routine copy localized', async () => {
    stub({
      agents: [agent('support_bot', 'No routine section here.')],
      next: { ...NEXT, agent: 'support_bot', next_wakes: [], error: 'windowed mode requires days' },
    });
    mount(`/orgs/${SLUG}/work-hours/support_bot`, 'zh-CN');
    expect(await screen.findByText('计划不完整：windowed mode requires days')).toBeInTheDocument();
    expect(screen.getByText('已被资格选择器排除 — 此计划已配置但不会生效。')).toBeInTheDocument();
    expect(screen.getByText(/该智能体的唤醒不会派发任何任务/)).toBeInTheDocument();
  });
});

describe('Work Hours wakes view i18n', () => {
  test('zh-CN list chrome, mapped statuses, unknown status verbatim, daemon text verbatim', async () => {
    stub();
    mount(`/orgs/${SLUG}/work-hours?view=wakes`, 'zh-CN');

    expect(await screen.findByText('Reviewed 3 PRs.')).toBeInTheDocument();
    expect(screen.getByText('工时 · 组织何时在线')).toBeInTheDocument();
    expect(screen.getByRole('heading', { name: '为你的智能体设定节奏。' })).toBeInTheDocument();
    expect(screen.getByText('仅供查看。此版本暂不支持创建命名的周期性唤醒。')).toBeInTheDocument();
    expect(screen.getByText('已完成')).toBeInTheDocument();
    expect(screen.getByText('失败')).toBeInTheDocument();
    expect(screen.getByText('weird_state')).toBeInTheDocument();
    expect(screen.getByText('2 个例行任务')).toBeInTheDocument();
    expect(screen.getByText('2 次唤醒')).toBeInTheDocument();
    expect(screen.getByText('1 次唤醒')).toBeInTheDocument();
    const eyebrow = screen.getByText(/，涉及/);
    expect(eyebrow).toHaveTextContent('3 次唤醒，涉及 2 个智能体');
    expect(screen.getByText('Executor exited 137')).toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'qa_engineer' })).toBeInTheDocument();

    const card = screen.getByRole('link', { name: 'dev_agent' });
    const requests = await countRequests(async () => {
      await switchLocale('en');
      expect(screen.getByText(/across/)).toHaveTextContent('3 wakes across 2 agents');
      expect(screen.getByText('1 routine')).toBeInTheDocument();
      expect(screen.getByText('1 wake')).toBeInTheDocument();
      expect(screen.getByText('Completed')).toBeInTheDocument();
      expect(screen.getByRole('link', { name: 'dev_agent' })).toBe(card);
      await switchLocale('zh-CN');
      expect(screen.getByRole('link', { name: 'dev_agent' })).toBe(card);
    });
    expect(requests).toEqual([]);
  });

  test('empty and error states are localized; error detail verbatim', async () => {
    stub({ wakes: [] });
    const { unmount } = mount(`/orgs/${SLUG}/work-hours?view=wakes`, 'zh-CN');
    expect(await screen.findByText('暂无已安排的唤醒')).toBeInTheDocument();
    unmount();

    stub({ wakesError: { detail: 'work_hours table unavailable' } });
    mount(`/orgs/${SLUG}/work-hours?view=wakes`, 'zh-CN');
    expect(await screen.findByText(/无法加载已安排的唤醒。/)).toHaveTextContent(
      '无法加载已安排的唤醒。 work_hours table unavailable',
    );
    expect(screen.queryByText(/API 500/)).toBeNull();
    expect(screen.getByRole('button', { name: '重试' })).toBeInTheDocument();
  });
});

describe('Work Hours dialogs keep draft and focus across locale switches', () => {
  test('TierEditorDialog: typed interval + focus survive en -> zh-CN -> en; 422 text verbatim under localized heading', async () => {
    server.use(
      http.put(`/api/v1/orgs/${SLUG}/settings/org`, () =>
        HttpResponse.json({ detail: { errors: ['interval 5h must evenly divide 24h'] } }, { status: 422 }),
      ),
    );
    renderWithProviders(
      <>
        <Routes>
          <Route
            path="/orgs/:slug/*"
            element={
              <TierEditorDialog
                open
                onOpenChange={() => {}}
                tier={{ kind: 'agent', agent: 'dev_agent' }}
                wh={wh()}
                agentTeam={{ dev_agent: 'eng' }}
                allAgents={['dev_agent']}
                onSaved={() => {}}
              />
            }
          />
        </Routes>
        {SWITCHES}
      </>,
      { route: `/orgs/${SLUG}/work-hours/dev_agent`, i18n: { adapter: savedLocaleAdapter('en') } },
    );

    const dialog = await screen.findByRole('dialog', { name: 'Edit override — dev_agent' });
    const input = within(dialog).getByPlaceholderText('2h');
    fireEvent.change(input, { target: { value: '5h' } });
    input.focus();
    expect(within(dialog).getByText('Tier: AGENT. The server validates the merged config on save — invalid edits are rejected and never written.')).toBeInTheDocument();

    const requests = await countRequests(async () => {
      await switchLocale('zh-CN');
      expect(screen.getByRole('dialog', { name: '编辑覆盖 — dev_agent' })).toBe(dialog);
      expect(within(dialog).getByText('层级：智能体。服务器会在保存时校验合并后的配置 — 无效的修改会被拒绝，且不会写入。')).toBeInTheDocument();
      expect(within(dialog).getByText('ⓘ 格式如 2h / 30m（由服务器校验 ≤ 时间窗长度）')).toBeInTheDocument();
      expect(within(dialog).getByText('继承：09:00（组织默认）')).toBeInTheDocument();
      expect(within(dialog).getByText('继承：America/Los_Angeles（团队：eng）')).toBeInTheDocument();
      expect(within(dialog).getAllByRole('button', { name: '重置' }).length).toBeGreaterThan(0);
      expect(within(dialog).getByText('window.start')).toBeInTheDocument();
      expect(within(dialog).getByPlaceholderText('2h')).toBe(input);
      expect(input).toHaveValue('5h');
      expect(document.activeElement).toBe(input);
      await switchLocale('en');
      expect(within(dialog).getByText('inherited: 09:00 (Org default)')).toBeInTheDocument();
      expect(input).toHaveValue('5h');
      expect(document.activeElement).toBe(input);
      await switchLocale('zh-CN');
    });
    expect(requests).toEqual([]);

    fireEvent.click(within(dialog).getByRole('button', { name: '保存' }));
    expect(await within(dialog).findByText('保存被拒绝 — 配置未写入。')).toBeInTheDocument();
    expect(within(dialog).getByText('interval 5h must evenly divide 24h')).toBeInTheDocument();
  });

  test('TierEditorDialog impact preview is localized with plural agent count', async () => {
    renderWithProviders(
      <>
        <TierEditorDialog
          open
          onOpenChange={() => {}}
          tier={{ kind: 'org' }}
          wh={wh()}
          agentTeam={{ dev_agent: 'eng', support_bot: 'eng' }}
          allAgents={['dev_agent', 'support_bot']}
          onSaved={() => {}}
        />
        {SWITCHES}
      </>,
      { i18n: { adapter: savedLocaleAdapter('zh-CN') } },
    );
    const dialog = await screen.findByRole('dialog', { name: '编辑组织默认值' });
    fireEvent.click(within(dialog).getByRole('button', { name: '查看影响…' }));
    expect(within(dialog).getByText(/此更改将改变/)).toHaveTextContent('此更改将改变 1 个智能体的生效计划：');
    expect(within(dialog).getByText('support_bot')).toBeInTheDocument();
    expect(within(dialog).getByRole('button', { name: '确认并保存' })).toBeInTheDocument();
    expect(within(dialog).getByRole('button', { name: '返回' })).toBeInTheDocument();
    await switchLocale('en');
    expect(within(dialog).getByText(/This change alters/)).toHaveTextContent(
      'This change alters the effective schedule of 1 agent:',
    );
  });

  test('EligibilityEditorDialog: toggled draft + focus survive en -> zh-CN -> en with zero requests', async () => {
    renderWithProviders(
      <>
        <EligibilityEditorDialog
          open
          onOpenChange={() => {}}
          wh={wh({ agents: { mode: 'whitelist', include: ['dev_agent'], exclude: [] } })}
          allAgents={['dev_agent', 'support_bot']}
          onSaved={() => {}}
        />
        {SWITCHES}
      </>,
      { i18n: { adapter: savedLocaleAdapter('en') } },
    );
    const dialog = await screen.findByRole('dialog', { name: 'Edit eligibility' });
    // Draft: add support_bot to the include list (first picker).
    const includeChip = within(dialog).getAllByRole('button', { name: 'support_bot' })[0];
    fireEvent.click(includeChip);
    expect(includeChip).toHaveAttribute('aria-pressed', 'true');
    includeChip.focus();
    expect(within(dialog).getByRole('status')).toHaveTextContent(
      'Resulting eligible set: 2 agents — dev_agent, support_bot',
    );

    const requests = await countRequests(async () => {
      await switchLocale('zh-CN');
      expect(screen.getByRole('dialog', { name: '编辑资格' })).toBe(dialog);
      expect(within(dialog).getByText('控制哪些智能体可参与工时的唯一组织级开关。选择器选项来自实时名册。')).toBeInTheDocument();
      expect(within(dialog).getByText('模式')).toBeInTheDocument();
      expect(within(dialog).getByText('包含')).toBeInTheDocument();
      expect(within(dialog).getByText('排除')).toBeInTheDocument();
      expect(within(dialog).getByText('whitelist')).toBeInTheDocument();
      expect(within(dialog).getByRole('status')).toHaveTextContent(
        '最终符合资格的集合：2 个智能体 — dev_agent, support_bot',
      );
      expect(within(dialog).getAllByRole('button', { name: 'support_bot' })[0]).toBe(includeChip);
      expect(includeChip).toHaveAttribute('aria-pressed', 'true');
      expect(document.activeElement).toBe(includeChip);
      await switchLocale('en');
      expect(within(dialog).getByRole('button', { name: 'Review impact…' })).toBeInTheDocument();
      expect(includeChip).toHaveAttribute('aria-pressed', 'true');
      expect(document.activeElement).toBe(includeChip);
      await switchLocale('zh-CN');
    });
    expect(requests).toEqual([]);

    fireEvent.click(within(dialog).getByRole('button', { name: '查看影响…' }));
    expect(within(dialog).getByText(/最终符合资格的集合/)).toHaveTextContent('最终符合资格的集合：2 个智能体。');
    expect(within(dialog).getByRole('button', { name: '确认并保存' })).toBeInTheDocument();
  });

  test('EligibilityEditorDialog: empty roster copy localized', async () => {
    renderWithProviders(
      <EligibilityEditorDialog open onOpenChange={() => {}} wh={wh()} allAgents={[]} onSaved={() => {}} />,
      { i18n: { adapter: savedLocaleAdapter('zh-CN') } },
    );
    const dialog = await screen.findByRole('dialog', { name: '编辑资格' });
    expect(within(dialog).getByText('名册中没有智能体。')).toBeInTheDocument();
    expect(within(dialog).getByRole('button', { name: '取消' })).toBeInTheDocument();
  });
});
