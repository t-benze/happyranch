/**
 * THR-118 W3b-1 — Tasks route family (list + detail + owned dialogs) i18n.
 *
 * Renders the real routed pages under the real I18nProvider and asserts:
 *  - zh-CN product copy for the list (populated / filter form / empty / error)
 *    and the detail (header, actions, lineage, rail, execution status, events);
 *  - authored and machine values (briefs, notes, agent names, task/thread/job
 *    IDs, status/block_kind/reason values, unknown flavor/state, raw event
 *    actions and payloads) stay byte-identical in both locales;
 *  - a locale switch keeps the same row/dialog/field nodes, typed draft and
 *    focus, and issues zero requests;
 *  - a mapped daemon error and a state-held validation message re-translate in
 *    place while an unmapped daemon code (including one equal to catalog text)
 *    renders verbatim; a code-less string diagnostic is verbatim too, and only
 *    a blank or non-string one falls back to the localized failure copy.
 */
import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { http, HttpResponse } from 'msw';
import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest';
import { AppRoutes } from '@/routes';
import { StatusBadge } from '@/design-system/patterns/StatusBadge';
import {
  I18nTestBoundary,
  LocaleTestSwitch,
  renderWithProviders,
  savedLocaleAdapter,
} from '@/test/render';
import { server } from '@/test/server';

const mermaidChunk = vi.hoisted(() => {
  let release!: () => void;
  const ready = new Promise<void>((resolve) => { release = resolve; });
  return { ready, release };
});
// External library-load delay; actual Markdown, lazy import and callers stay real.
vi.mock('mermaid', async () => {
  await mermaidChunk.ready;
  return { default: { initialize: vi.fn(), render: vi.fn(async () => ({ svg: '<svg data-w5a-diagram="loaded"></svg>' })) } };
});

const SLUG = 'alpha';
const BRIEF = 'Ship `v2` checkout — keep **authored** text';
const NOTE = 'needs founder sign-off on refund_policy';

function rootTask(overrides: Record<string, unknown> = {}) {
  return {
    task_id: 'TASK-77',
    team: 'engineering',
    brief: BRIEF,
    status: 'in_progress',
    block_kind: 'delegated',
    assigned_agent: 'engineering_manager',
    parent_task_id: null,
    revisit_of_task_id: null,
    created_at: '2026-05-18T10:00:00Z',
    updated_at: new Date(Date.now() - 5 * 60_000).toISOString(),
    closed_at: null,
    cancelled_at: null,
    session_timeout_seconds: null,
    severity_rollup: 'failed',
    dispatched_from_thread_id: 'THR-9',
    ...overrides,
  };
}

function stubBase() {
  server.use(
    http.get('/api/v1/orgs', () => HttpResponse.json({ orgs: [{ slug: SLUG, root: '/x' }] })),
    http.get('/api/v1/orgs/:slug/dashboard/summary', () => HttpResponse.json({ org_age_days: 1 })),
  );
}

function stubRoots(tasks: unknown[], escalated: unknown[] = []) {
  server.use(
    http.get(`/api/v1/orgs/${SLUG}/tasks/roots`, ({ request }) => {
      const status = new URL(request.url).searchParams.get('status');
      return HttpResponse.json({
        tasks: status === 'escalated' ? escalated : tasks,
        next_cursor: null,
      });
    }),
  );
}

function stubDetail(task: ReturnType<typeof rootTask>, extra: Record<string, unknown> = {}) {
  const id = task.task_id;
  server.use(
    http.get(`/api/v1/orgs/${SLUG}/tasks/${id}`, () =>
      HttpResponse.json({
        task,
        results: [],
        audit_log: [],
        revisit_chain: [id, 'TASK-70'],
        direct_revisits: ['TASK-80'],
        predecessor_prior_status: null,
        work_status: {
          applicable: true,
          state: 'newly_started',
          label: 'Newly started — awaiting first update',
          reason: null,
          session_start_ts: '2026-05-18T10:00:00Z',
          heartbeat: { timestamp: '2026-05-18T10:01:00Z', freshness: 'fresh' },
          latest_progress: null,
        },
        ...extra,
      }),
    ),
    http.get(`/api/v1/orgs/${SLUG}/tasks/${id}/recall`, () =>
      HttpResponse.json({
        task_id: id,
        assigned_agent: 'engineering_manager',
        brief: BRIEF,
        status: task.status,
        output_summary: null,
        children: [
          {
            task_id: 'TASK-78',
            assigned_agent: 'frontend_engineer',
            brief: 'Child brief stays verbatim',
            status: 'failed',
            output_summary: null,
            children: [],
          },
        ],
      }),
    ),
    http.get(`/api/v1/orgs/${SLUG}/tasks/${id}/events`, () =>
      HttpResponse.text('', { headers: { 'content-type': 'text/event-stream' } }),
    ),
    http.get(`/api/v1/orgs/${SLUG}/jobs`, () =>
      HttpResponse.json({
        jobs: [{ id: 'JOB-5', title: 'Authored job title', status: 'running', task_id: id }],
      }),
    ),
  );
}

function mount(route: string, locale: 'en' | 'zh-CN') {
  return renderWithProviders(
    <>
      <AppRoutes />
      <LocaleTestSwitch to="zh-CN" />
      <LocaleTestSwitch to="en" />
    </>,
    { route, i18n: { adapter: savedLocaleAdapter(locale) } },
  );
}

/** Switch locale without moving focus (fireEvent.click never focuses). */
async function switchLocale(to: 'en' | 'zh-CN') {
  await act(async () => {
    fireEvent.click(screen.getByTestId(`test-set-locale-${to}`));
  });
}

/** Record every request issued while `fn` runs (incl. effect-triggered refetches). */
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
  stubBase();
});

afterEach(() => {
  vi.restoreAllMocks();
});

describe('Tasks list i18n', () => {
  test('populated list: zh-CN chrome, verbatim data, same row node and zero requests across switches', async () => {
    const waiting = rootTask({
      task_id: 'TASK-90',
      status: 'escalated',
      block_kind: null,
      severity_rollup: 'escalated',
      assigned_agent: null,
      dispatched_from_thread_id: null,
      brief: 'Escalated brief stays verbatim',
    });
    stubRoots([rootTask()], [waiting]);
    mount(`/orgs/${SLUG}/tasks`, 'zh-CN');

    expect(await screen.findByRole('heading', { name: '组织正在处理的工作' })).toBeInTheDocument();
    expect(await screen.findByText('已加载 1 个匹配的根任务 · 子任务向上汇总 · 1 个失败')).toBeInTheDocument();
    const tabs = screen.getByRole('tablist', { name: '分组方式' });
    expect(within(tabs).getByRole('tab', { name: '状态' })).toBeInTheDocument();
    expect(within(tabs).getByRole('tab', { name: '智能体' })).toBeInTheDocument();
    expect(within(tabs).getByRole('tab', { name: '会话' })).toBeInTheDocument();
    expect(screen.getByRole('heading', { name: '等你处理' })).toBeInTheDocument();
    expect(screen.getByText('1 个等你处理')).toBeInTheDocument();
    expect(screen.getByRole('heading', { name: /进行中/ })).toBeInTheDocument();
    for (const column of ['状态', '任务', '标题', '智能体', '会话', '更新']) {
      expect(screen.getAllByText(column).length).toBeGreaterThan(0);
    }

    const row = screen.getByRole('link', { name: /TASK-77/ });
    expect(row.textContent).toContain('等待子任务');
    expect(row.textContent).toContain('子任务失败');
    expect(row.textContent).toContain('5 分钟');
    // Authored brief headline, IDs and agent name are byte-identical.
    expect(within(row).getByTitle(BRIEF).textContent).toBe(BRIEF);
    expect(row.textContent).toContain('TASK-77');
    expect(row.textContent).toContain('THR-9');
    expect(row.textContent).toContain('engineering_manager');
    // A raw status machine value (StatusBadge text) is never translated.
    expect(row.textContent).toContain('in_progress');

    const requests = await countRequests(async () => {
      await switchLocale('en');
      expect(screen.getByRole('link', { name: /TASK-77/ })).toBe(row);
      expect(row.textContent).toContain('waiting on subtasks');
      expect(row.textContent).toContain('subtask failed');
      expect(row.textContent).toContain('5m');
      expect(screen.getByRole('heading', { name: 'What the org is working on' })).toBeInTheDocument();
      expect(screen.getByText('1 waiting on you')).toBeInTheDocument();
      await switchLocale('zh-CN');
      expect(screen.getByRole('link', { name: /TASK-77/ })).toBe(row);
      expect(row.textContent).toContain('等待子任务');
    });
    expect(requests).toEqual([]);
  });

  test('agent/thread grouping: sentinel groups re-label in place without remounting', async () => {
    stubRoots([rootTask({ assigned_agent: null, dispatched_from_thread_id: null })]);
    const user = userEvent.setup();
    mount(`/orgs/${SLUG}/tasks`, 'en');

    await screen.findByRole('link', { name: /TASK-77/ });
    await user.click(screen.getByRole('tab', { name: 'Agent' }));
    const unassigned = await screen.findByRole('heading', { name: /^Unassigned/ });
    await switchLocale('zh-CN');
    expect(screen.getByRole('heading', { name: /^未分配/ })).toBe(unassigned);
    await user.click(screen.getByRole('tab', { name: '会话' }));
    const noThread = await screen.findByRole('heading', { name: /^无会话/ });
    await switchLocale('en');
    expect(screen.getByRole('heading', { name: /^No thread/ })).toBe(noThread);
  });

  test('filter form: draft input keeps node, value and focus across switches with zero requests', async () => {
    stubRoots([rootTask()]);
    const user = userEvent.setup();
    mount(`/orgs/${SLUG}/tasks`, 'en');

    await screen.findByRole('link', { name: /TASK-77/ });
    await user.click(screen.getByRole('button', { name: 'Filter' }));
    const form = screen.getByRole('form', { name: 'Task filters' });
    const input = within(form).getByRole('textbox');
    await user.type(input, 'frontend_engineer');
    expect(input).toHaveFocus();

    const requests = await countRequests(async () => {
      await switchLocale('zh-CN');
      const zhForm = screen.getByRole('form', { name: '任务筛选' });
      expect(zhForm).toBe(form);
      expect(within(zhForm).getByRole('textbox')).toBe(input);
      expect(input).toHaveValue('frontend_engineer');
      expect(input).toHaveFocus();
      const select = within(zhForm).getByRole('combobox', { name: '任务状态' });
      expect(within(select).getByRole('option', { name: '全部状态' })).toBeInTheDocument();
      // Status option labels are the raw machine values.
      expect(within(select).getByRole('option', { name: 'in_progress' })).toBeInTheDocument();
      expect(within(zhForm).getByRole('button', { name: '应用' })).toBeInTheDocument();
      await switchLocale('en');
      expect(input).toHaveValue('frontend_engineer');
      expect(input).toHaveFocus();
    });
    expect(requests).toEqual([]);
  });

  test('empty and error states are localized', async () => {
    stubRoots([]);
    const first = mount(`/orgs/${SLUG}/tasks`, 'zh-CN');
    expect(await screen.findByText('没有任务')).toBeInTheDocument();
    expect(screen.getByText('没有符合当前筛选条件的任务。')).toBeInTheDocument();
    first.unmount();

    server.use(
      http.get(`/api/v1/orgs/${SLUG}/tasks/roots`, () =>
        HttpResponse.json({ detail: 'boom' }, { status: 500 }),
      ),
    );
    mount(`/orgs/${SLUG}/tasks`, 'zh-CN');
    expect(await screen.findByText('无法加载任务', {}, { timeout: 5000 })).toBeInTheDocument();
    expect(screen.getByText('服务器返回了错误。你可以重试。')).toBeInTheDocument();
    expect(screen.getAllByRole('button', { name: /重试/ }).length).toBeGreaterThan(0);
  });
});

describe('Task detail i18n', () => {
  test('detail chrome localized; authored and machine values verbatim; zero requests on switch', async () => {
    const task = rootTask({ status: 'escalated', block_kind: null, note: NOTE });
    stubDetail(task, {
      audit_log: [{ action: 'escalation', payload: { reason: 'something novel' } }],
    });
    mount(`/orgs/${SLUG}/tasks/TASK-77`, 'zh-CN');

    expect(await screen.findByRole('button', { name: '继续' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '取消' })).toBeInTheDocument();
    expect(screen.getByRole('link', { name: '‹ 全部任务' })).toBeInTheDocument();
    expect(await screen.findByText('· 待决策')).toBeInTheDocument();
    expect(screen.getByText('升级原因：')).toBeInTheDocument();
    expect(screen.getByText(NOTE)).toBeInTheDocument();
    expect(await screen.findByText('重做与依赖链')).toBeInTheDocument();
    expect(screen.getByText('重做自')).toBeInTheDocument();
    expect(screen.getByText('当前任务')).toBeInTheDocument();
    expect(screen.getByText('被重做为')).toBeInTheDocument();
    const rail = screen.getByRole('complementary', { name: '任务状态与属性' });
    expect(within(rail).getByText('负责人')).toBeInTheDocument();
    expect(within(rail).getByText('engineering_manager')).toBeInTheDocument();
    const exec = within(rail).getByRole('region', { name: '执行状态' });
    expect(exec.textContent).toContain('刚刚开始 — 等待首次更新');
    expect(exec.textContent).toContain('（新鲜）');
    expect(await screen.findByText('此任务的作业')).toBeInTheDocument();
    expect(screen.getByText(/Authored job title/).textContent).toContain('(running)');
    expect(await screen.findByText('执行子任务')).toBeInTheDocument();
    expect(screen.getAllByText('Child brief stays verbatim').length).toBeGreaterThan(0);

    const continueBtn = screen.getByRole('button', { name: '继续' });
    const thisTaskNode = screen.getByText('当前任务').closest('li');
    expect(thisTaskNode).not.toBeNull();
    const requests = await countRequests(async () => {
      await switchLocale('en');
      expect(screen.getByRole('button', { name: 'Continue' })).toBe(continueBtn);
      expect(screen.getByText('This task').closest('li')).toBe(thisTaskNode);
      expect(screen.getByText('· needs-decision')).toBeInTheDocument();
      expect(screen.getByText('Revisit & dependency chain')).toBeInTheDocument();
      expect(screen.getByText(NOTE)).toBeInTheDocument();
      await switchLocale('zh-CN');
      expect(screen.getByRole('button', { name: '继续' })).toBe(continueBtn);
    });
    expect(requests).toEqual([]);
  });

  test('authority-v2 escalation labels localized; daemon primary/secondary verbatim in both locales', async () => {
    const PRIMARY = 'Founder must choose the supported host action.';
    const SECONDARY = "Automatic continuation couldn't be committed, so this was escalated to you.";
    const task = rootTask({
      status: 'escalated',
      block_kind: null,
      note: 'authority_v2_refusal:final_commit_failed',
    });
    stubDetail(task, {
      escalation_reason: { primary: PRIMARY, refusal_code: 'final_commit_failed', secondary: SECONDARY },
    });
    mount(`/orgs/${SLUG}/tasks/TASK-77`, 'zh-CN');

    expect(await screen.findByText('升级原因：')).toBeInTheDocument();
    expect(screen.getByText('自动升级：')).toBeInTheDocument();
    expect(screen.getByText('升级原因：').nextElementSibling?.textContent).toBe(PRIMARY);
    expect(screen.getByText('自动升级：').nextElementSibling?.textContent).toBe(SECONDARY);
    expect(screen.queryByText('Escalation reason:')).not.toBeInTheDocument();
    expect(screen.queryByText('Automatic escalation:')).not.toBeInTheDocument();
    expect(screen.queryByText('authority_v2_refusal:final_commit_failed')).not.toBeInTheDocument();

    await switchLocale('en');
    expect(screen.getByText('Escalation reason:').nextElementSibling?.textContent).toBe(PRIMARY);
    expect(screen.getByText('Automatic escalation:').nextElementSibling?.textContent).toBe(SECONDARY);
    expect(screen.queryByText('升级原因：')).not.toBeInTheDocument();
    expect(screen.queryByText('自动升级：')).not.toBeInTheDocument();
  });

  test('unknown flavor, block_kind, work-status state and reason render verbatim', async () => {
    const task = rootTask({ status: 'in_progress', block_kind: 'awaiting_quorum' });
    stubDetail(task, {
      work_status: {
        applicable: false,
        state: 'future_state',
        label: 'Daemon label verbatim',
        reason: 'no_session_start',
        session_start_ts: null,
        heartbeat: { timestamp: null, freshness: 'unavailable' },
        latest_progress: null,
      },
    });
    mount(`/orgs/${SLUG}/tasks/TASK-77`, 'zh-CN');

    const rail = await screen.findByRole('complementary', { name: '任务状态与属性' });
    const exec = await within(rail).findByRole('region', { name: '执行状态' });
    expect(exec.textContent).toContain('Daemon label verbatim');
    expect(exec.textContent).toContain('no_session_start');
    expect(exec.textContent).toContain('未观测到');
    expect(within(rail).getByText('阻塞类型')).toBeInTheDocument();
    expect(within(rail).getAllByText('awaiting_quorum').length).toBeGreaterThan(0);
  });

  test('fan-out band and chain timeline: localized copy, counts/ids/join mode verbatim', async () => {
    const task = rootTask({
      active_fanout: JSON.stringify({ status: 'spawned', width: 2, children_ids: ['TASK-78'] }),
    });
    stubDetail(task, {
      active_chain: {
        step_index: 1,
        first_leg_expect_verdict: 'APPROVE',
        legs: [{ agent: 'code_reviewer', expect_verdict: 'PASS' }],
      },
    });
    mount(`/orgs/${SLUG}/tasks/TASK-77`, 'zh-CN');

    const band = await screen.findByRole('region', { name: '扇出状态' });
    await waitFor(() => expect(band.textContent).toContain('扇出运行中 — 已完成 1/2'));
    expect(band.textContent).toContain('所有子任务都进入终止状态后，父任务会自动恢复。');
    expect(band.textContent).toContain('已完成 0/1 · 1 个失败');
    expect(band.textContent).toContain('宽度 2');
    expect(band.textContent).toContain('汇合 all-terminal');
    expect(await screen.findByText('工作流链 — 第 2 步，共 2 步')).toBeInTheDocument();
    expect(screen.getByText('第 1 段（首段）')).toBeInTheDocument();
    expect(screen.getByText('APPROVE')).toBeInTheDocument();
    expect(screen.getByText('code_reviewer')).toBeInTheDocument();
    expect(screen.getByText('阻塞于：').textContent).toBe('阻塞于：等待 2 个子任务');
    // Header + rail StatusBadge: raw status value, localized waiting qualifier.
    expect(screen.getAllByText('· 等待子任务')).toHaveLength(2);
    await switchLocale('en');
    expect(band.textContent).toContain('Running fan-out — 1 of 2 done');
    expect(screen.getByText('Blocked on:').textContent).toBe('Blocked on: waiting on 2 subtasks');
  });

  test('cancel dialog draft survives both switches; mapped error retranslates, raw codes stay verbatim', async () => {
    stubDetail(rootTask());
    const reply = { kind: 'mapped' as 'mapped' | 'raw' | 'catalog-equal', posts: 0 };
    server.use(
      http.post(`/api/v1/orgs/${SLUG}/tasks/TASK-77/cancel`, () => {
        reply.posts += 1;
        const code =
          reply.kind === 'mapped'
            ? 'not_found'
            : reply.kind === 'raw'
              ? 'brand_new_code'
              : 'Task not found.';
        return HttpResponse.json({ detail: { code } }, { status: 400 });
      }),
    );
    const user = userEvent.setup();
    mount(`/orgs/${SLUG}/tasks/TASK-77`, 'en');

    await user.click(await screen.findByRole('button', { name: 'Cancel' }));
    const dialog = await screen.findByRole('dialog', { name: 'Cancel task' });
    const close = within(dialog).getByRole('button', { name: 'Close' });
    const textarea = within(dialog).getByPlaceholderText('Reason for cancellation (optional)');
    await user.type(textarea, 'draft reason');
    expect(textarea).toHaveFocus();

    const requests = await countRequests(async () => {
      await switchLocale('zh-CN');
      expect(screen.getByRole('dialog', { name: '取消任务' })).toBe(dialog);
      expect(within(dialog).getByRole('button', { name: '关闭' })).toBe(close);
      expect(within(dialog).getByPlaceholderText('取消原因（可选）')).toBe(textarea);
      expect(textarea).toHaveValue('draft reason');
      expect(textarea).toHaveFocus();
      await switchLocale('en');
      expect(screen.getByRole('dialog', { name: 'Cancel task' })).toBe(dialog);
      expect(within(dialog).getByRole('button', { name: 'Close' })).toBe(close);
      expect(textarea).toHaveValue('draft reason');
      expect(textarea).toHaveFocus();
    });
    expect(requests).toEqual([]);

    await user.click(within(dialog).getByRole('button', { name: 'Cancel task' }));
    expect(await within(dialog).findByText('Task not found.')).toBeInTheDocument();
    expect(reply.posts).toBe(1);
    await switchLocale('zh-CN');
    expect(within(dialog).getByText('未找到任务。')).toBeInTheDocument();
    expect(within(dialog).queryByText('Task not found.')).toBeNull();
    expect(reply.posts).toBe(1);

    reply.kind = 'raw';
    await user.click(within(dialog).getByRole('button', { name: '取消任务' }));
    expect(await within(dialog).findByText('brand_new_code')).toBeInTheDocument();
    await switchLocale('en');
    expect(within(dialog).getByText('brand_new_code')).toBeInTheDocument();

    // A raw code that happens to equal English catalog text is still raw.
    reply.kind = 'catalog-equal';
    await user.click(within(dialog).getByRole('button', { name: 'Cancel task' }));
    await waitFor(() => expect(reply.posts).toBe(3));
    await switchLocale('zh-CN');
    expect(await within(dialog).findByText('Task not found.')).toBeInTheDocument();
    expect(within(dialog).queryByText('未找到任务。')).toBeNull();
    expect(textarea).toHaveValue('draft reason');
  });

  test('code-less errors: a string diagnostic stays verbatim, a blank or non-string one falls back', async () => {
    stubDetail(rootTask());
    type Reply =
      | { kind: 'http'; status: number; body: string }
      | { kind: 'reject'; value: unknown };
    let reply: Reply = { kind: 'http', status: 500, body: '' };
    let attempts = 0;
    const realFetch = globalThis.fetch;
    vi.spyOn(globalThis, 'fetch').mockImplementation((input, init) => {
      const url = typeof input === 'string' ? input : input instanceof URL ? input.href : input.url;
      if (!url.endsWith(`/tasks/TASK-77/cancel`)) return realFetch(input, init);
      attempts += 1;
      if (reply.kind === 'reject') return Promise.reject(reply.value);
      return Promise.resolve(
        new Response(reply.body, {
          status: reply.status,
          headers: { 'content-type': 'application/json' },
        }),
      );
    });
    const user = userEvent.setup();
    mount(`/orgs/${SLUG}/tasks/TASK-77`, 'en');

    await user.click(await screen.findByRole('button', { name: 'Cancel' }));
    const dialog = await screen.findByRole('dialog', { name: 'Cancel task' });
    const textarea = within(dialog).getByPlaceholderText('Reason for cancellation (optional)');

    /** Submit once (en), then read the shown error in en and in zh-CN. */
    async function attempt(next: Reply): Promise<{ en: string; zh: string }> {
      reply = next;
      const before = attempts;
      const shown = () => textarea.nextElementSibling;
      const previous = shown();
      await user.click(within(dialog).getByRole('button', { name: 'Cancel task' }));
      await waitFor(() => expect(attempts).toBe(before + 1));
      // setError(null) unmounts the old message first, so a new node is a new result.
      await waitFor(() => {
        expect(shown()?.tagName).toBe('P');
        expect(shown()).not.toBe(previous);
      });
      const en = shown()!.textContent ?? '';
      await switchLocale('zh-CN');
      const zh = shown()!.textContent ?? '';
      await switchLocale('en');
      return { en, zh };
    }
    const json = (status: number, body: unknown): Reply => ({
      kind: 'http',
      status,
      body: JSON.stringify(body),
    });

    // (c) ApiError with a null code and a plain-string daemon detail: verbatim.
    const DIAG = 'TASK-77 is already terminal (completed); nothing to cancel';
    expect(await attempt(json(409, { detail: DIAG }))).toEqual({ en: DIAG, zh: DIAG });
    // (c) detail equal to English catalog text is still raw — never retranslated.
    expect(await attempt(json(409, { detail: 'Cancel failed.' }))).toEqual({
      en: 'Cancel failed.',
      zh: 'Cancel failed.',
    });
    // (c) a plain Error and a plain string rejection: their text, verbatim.
    expect(await attempt({ kind: 'reject', value: new Error('socket hang up') })).toEqual({
      en: 'socket hang up',
      zh: 'socket hang up',
    });
    expect(await attempt({ kind: 'reject', value: 'upstream reset' })).toEqual({
      en: 'upstream reset',
      zh: 'upstream reset',
    });

    // (d) no code and no non-empty string diagnostic: the localized fallback,
    // never ApiError's synthetic 'API 500' message and never a blank error.
    const FALLBACK = { en: 'Cancel failed.', zh: '取消失败。' };
    expect(await attempt({ kind: 'http', status: 500, body: '' })).toEqual(FALLBACK);
    expect(await attempt(json(500, { detail: '' }))).toEqual(FALLBACK);
    expect(await attempt(json(500, { detail: '   ' }))).toEqual(FALLBACK);
    expect(await attempt(json(500, { detail: { message: 'object detail' } }))).toEqual(FALLBACK);
    expect(await attempt(json(500, { detail: { code: '' } }))).toEqual(FALLBACK);
    expect(await attempt({ kind: 'reject', value: new Error('   ') })).toEqual(FALLBACK);
    expect(await attempt({ kind: 'reject', value: null })).toEqual(FALLBACK);
    expect(screen.queryByText(/^API 500/)).toBeNull();
  });

  test('revisit validation message is state-held and retranslates without a request', async () => {
    stubDetail(rootTask({ status: 'completed', block_kind: null }));
    const user = userEvent.setup();
    mount(`/orgs/${SLUG}/tasks/TASK-77`, 'zh-CN');

    await user.click(await screen.findByRole('button', { name: '重做' }));
    const dialog = await screen.findByRole('dialog', { name: '重做任务' });
    const close = within(dialog).getByRole('button', { name: '关闭' });
    const timeout = within(dialog).getByPlaceholderText('会话超时（秒，可选）');
    await user.type(timeout, 'soon');
    const requests = await countRequests(async () => {
      await user.click(within(dialog).getByRole('button', { name: '重做' }));
      expect(within(dialog).getByText('会话超时必须是正整数。')).toBeInTheDocument();
      await switchLocale('en');
      expect(within(dialog).getByText('Session timeout must be a positive integer.')).toBeInTheDocument();
      expect(within(dialog).getByRole('button', { name: 'Close' })).toBe(close);
      expect(timeout).toHaveValue('soon');
      await switchLocale('zh-CN');
      expect(within(dialog).getByRole('button', { name: '关闭' })).toBe(close);
    });
    expect(requests).toEqual([]);
  });

  test('continue dialog close names follow both locale switches without replacing the draft or posting', async () => {
    stubDetail(rootTask({ status: 'escalated', block_kind: 'escalated' }));
    const user = userEvent.setup();
    mount(`/orgs/${SLUG}/tasks/TASK-77`, 'en');
    await user.click(await screen.findByRole('button', { name: 'Continue' }));
    const dialog = await screen.findByRole('dialog', { name: 'Continue task' });
    const closeButtons = within(dialog).getAllByRole('button', { name: 'Close' });
    expect(closeButtons).toHaveLength(2); // Footer action and the built-in X.
    const field = within(dialog).getByPlaceholderText('Rationale (required)');
    await user.type(field, 'Keep this authored rationale');
    const requests = await countRequests(async () => {
      await switchLocale('zh-CN');
      expect(within(dialog).getAllByRole('button', { name: '关闭' })).toEqual(closeButtons);
      expect(within(dialog).queryByRole('button', { name: 'Close' })).toBeNull();
      await switchLocale('en');
      expect(within(dialog).getAllByRole('button', { name: 'Close' })).toEqual(closeButtons);
      expect(field).toHaveValue('Keep this authored rationale');
      expect(field).toHaveFocus();
      expect(screen.getByRole('dialog', { name: 'Continue task' })).toBe(dialog);
    });
    expect(requests).toEqual([]);
    await user.click(closeButtons[1]!);
    await waitFor(() => expect(screen.queryByRole('dialog', { name: 'Continue task' })).toBeNull());
    expect(dialog.isConnected).toBe(false);
  });
});

describe('shared StatusBadge seam', () => {
  test('omitting waitingLabels keeps the English qualifier for other callers in any locale', () => {
    render(
      <>
        <StatusBadge status="in_progress" blockKind="delegated" />
        <StatusBadge status="in_progress" blockKind="blocked_on_job" waitingLabels={{ blocked_on_job: '等待作业' }} />
      </>,
      { wrapper: I18nTestBoundary },
    );
    expect(screen.getByText('· waiting on subtasks')).toBeInTheDocument();
    expect(screen.getByText('· 等待作业')).toBeInTheDocument();
  });
});

test('Task detail and recall forward Mermaid loading copy while the cancel draft stays focused with zero requests', async () => {
  const body = 'Rendering diagram…\n\n```mermaid\nflowchart LR; A-->B\n```';
  stubDetail(rootTask({ brief: body }));
  server.use(http.get(`/api/v1/orgs/${SLUG}/tasks/TASK-77/recall`, () => HttpResponse.json({
    task_id: 'TASK-77', assigned_agent: 'engineering_manager', brief: body,
    status: 'in_progress', output_summary: null, children: [],
  })));
  const view = mount(`/orgs/${SLUG}/tasks/TASK-77`, 'en');
  try {
    await waitFor(() => expect(view.container.querySelectorAll('.gl-prose-mermaid-loading')).toHaveLength(2));
    const fallbacks = Array.from(view.container.querySelectorAll('.gl-prose-mermaid-loading'));
    await userEvent.click(screen.getByRole('button', { name: 'Cancel' }));
    const dialog = screen.getByRole('dialog', { name: /Cancel/ });
    const input = within(dialog).getByRole('textbox');
    fireEvent.change(input, { target: { value: 'raw draft /任务' } }); input.focus();
    const requests = await countRequests(async () => {
      for (const locale of ['zh-CN', 'en'] as const) {
        await switchLocale(locale);
        expect(fallbacks.map(node => node.textContent)).toEqual(fallbacks.map(() => locale === 'en' ? 'Rendering diagram…' : '正在渲染图表…'));
        expect(Array.from(view.container.querySelectorAll('.gl-prose-mermaid-loading'))).toEqual(fallbacks);
        expect(within(dialog).getByRole('textbox')).toBe(input);
        expect(input).toHaveFocus(); expect(input).toHaveValue('raw draft /任务');
      }
    });
    expect(requests).toEqual([]);
  } finally { await act(async () => mermaidChunk.release()); }
  await waitFor(() => expect(view.container.querySelectorAll('svg[data-w5a-diagram]')).toHaveLength(2));
});
