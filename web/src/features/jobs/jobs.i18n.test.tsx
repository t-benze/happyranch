/**
 * THR-118 W3b-2 — Jobs route family (list + detail + owned dialogs) i18n.
 *
 * Renders the real routed pages under the real I18nProvider and asserts:
 *  - zh-CN product copy for the list (populated / empty / error) and the
 *    detail (header actions, command card, cascade, gated notice, rail,
 *    output) and the Run/Reject dialogs;
 *  - daemon-supplied values (job/task IDs, titles, commands, rationale, agent
 *    names, status tokens, exit codes, stdout) stay byte-identical;
 *  - a locale switch keeps the same card/dialog/field nodes, the typed draft
 *    and focus, and issues zero requests;
 *  - the diagnostic boundary through the routed dialogs: a recognized daemon
 *    code maps to catalog copy (re-translated in place), an unknown code and a
 *    code-less string detail stay verbatim, and an empty code / blank or
 *    non-string detail falls back to the localized failure copy.
 */
import { act, fireEvent, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { http, HttpResponse } from 'msw';
import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest';
import { AppRoutes } from '@/routes';
import { LocaleTestSwitch, renderWithProviders, savedLocaleAdapter } from '@/test/render';
import { server } from '@/test/server';
import type { JobRecord } from '@/lib/api/types';

const SLUG = 'alpha';

const JOB: JobRecord = {
  id: 'JOB-0001',
  task_id: 'TASK-0042',
  agent_name: 'engineering_head',
  title: 'Clean up stale Docker images',
  rationale: 'Disk usage is above 90% on the build server.',
  script_text: 'docker image prune -af',
  interpreter: 'bash',
  cwd_hint: null,
  status: 'pending',
  exit_code: null,
  stdout_head: null,
  stderr_head: null,
  stdout_path: null,
  stderr_path: null,
  duration_ms: null,
  started_at: null,
  finished_at: null,
  reviewed_at: null,
  reviewed_by: null,
  reject_reason: null,
  cwd_resolved: null,
  max_runtime_seconds: 300,
  max_output_bytes: 52428800,
  review_required: true,
  persistent: false,
  reason: null,
  created_at: '2026-05-23T12:00:00Z',
};

function job(overrides: Partial<JobRecord>): JobRecord {
  return { ...JOB, ...overrides };
}

const LIST: JobRecord[] = [
  job({ id: 'JOB-0005', status: 'pending', agent_name: 'senior_dev', task_id: 'TASK-0548' }),
  job({ id: 'JOB-0002', status: 'running', script_text: 'data-pipeline upload --all' }),
  job({ id: 'JOB-0003', status: 'completed', exit_code: 0, review_required: false }),
  job({ id: 'JOB-0004', status: 'rejected', script_text: "psql -c 'TRUNCATE guides;'" }),
];

function stubBase() {
  server.use(
    http.get('/api/v1/orgs', () => HttpResponse.json({ orgs: [{ slug: SLUG, root: '/x' }] })),
  );
}

function stubList(mode: { jobs?: JobRecord[]; error?: boolean } = {}) {
  server.use(
    http.get(`/api/v1/orgs/${SLUG}/jobs/`, ({ request }) => {
      if (mode.error) return HttpResponse.json({ detail: 'boom' }, { status: 500 });
      const jobs = mode.jobs ?? LIST;
      const status = new URL(request.url).searchParams.get('status');
      const out = status && status !== 'all' ? jobs.filter((j) => j.status === status) : jobs;
      return HttpResponse.json({ jobs: out, next_cursor: null });
    }),
  );
}

function stubDetail(record: JobRecord) {
  server.use(
    http.get(`/api/v1/orgs/${SLUG}/jobs/`, () => HttpResponse.json({ jobs: [], next_cursor: null })),
    http.get(`/api/v1/orgs/${SLUG}/jobs/${record.id}`, () => HttpResponse.json(record)),
    http.get(`/api/v1/orgs/${SLUG}/tasks`, () => HttpResponse.json({ tasks: [], next_cursor: null })),
    http.get(`/api/v1/orgs/${SLUG}/jobs/${record.id}/tail`, ({ request }) =>
      HttpResponse.json({ stream: new URL(request.url).searchParams.get('stream'), lines: [] }),
    ),
    http.get(`/api/v1/orgs/${SLUG}/jobs/${record.id}/events`, () =>
      HttpResponse.text('', { headers: { 'content-type': 'text/event-stream' } }),
    ),
    http.get(`/api/v1/orgs/${SLUG}/jobs/${record.id}/output`, () =>
      HttpResponse.json({
        stdout: 'Deleted 3 images\n',
        stderr: '',
        truncated_stdout: false,
        truncated_stderr: false,
        total_stdout_bytes: 18,
        total_stderr_bytes: 0,
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

/** The job dialog owns the named control (the assistant dock is also a dialog). */
function dialogOwning(control: HTMLElement): HTMLElement {
  const dialog = control.closest('[role="dialog"]');
  if (!(dialog instanceof HTMLElement)) throw new Error('control is not inside a dialog');
  return dialog;
}

beforeEach(() => {
  sessionStorage.setItem('happyranch.token', 'tok');
  localStorage.clear();
  stubBase();
});

afterEach(() => {
  vi.restoreAllMocks();
});

describe('Jobs list i18n', () => {
  test('populated list: zh-CN chrome, verbatim daemon values, same card node and zero requests across switches', async () => {
    stubList();
    mount(`/orgs/${SLUG}/jobs`, 'zh-CN');

    expect(await screen.findByRole('heading', { name: '等待决定的命令' })).toBeInTheDocument();
    expect(screen.getByText('需创始人把关的命令 · 智能体提议，由你批准')).toBeInTheDocument();
    expect(await screen.findByText('1 个作业需要你处理')).toBeInTheDocument();
    for (const column of ['作业', '命令', '请求者', '任务', '发起']) {
      expect(screen.getAllByText(column).length).toBeGreaterThan(0);
    }
    for (const group of ['等待你批准', '运行中', '已完成', '已拒绝']) {
      expect(screen.getByRole('region', { name: group })).toBeInTheDocument();
    }
    expect(screen.getByText('需要审核')).toBeInTheDocument();
    // Daemon values stay byte-verbatim.
    expect(screen.getByText('JOB-0005')).toBeInTheDocument();
    expect(screen.getByText('senior_dev')).toBeInTheDocument();
    expect(screen.getByText('TASK-0548')).toBeInTheDocument();
    expect(screen.getByText("$ psql -c 'TRUNCATE guides;'")).toBeInTheDocument();
    expect(screen.getByText('running')).toBeInTheDocument();
    expect(screen.getByText('exit 0')).toBeInTheDocument();
    expect(screen.getByText('rejected')).toBeInTheDocument();

    const card = screen.getByText('JOB-0005').closest('a');
    expect(card).not.toBeNull();
    const requests = await countRequests(async () => {
      await switchLocale('en');
      expect(screen.getByRole('heading', { name: 'Commands awaiting a decision' })).toBeInTheDocument();
      expect(screen.getByText('1 job needs you')).toBeInTheDocument();
      expect(screen.getByRole('region', { name: 'Awaiting your approval' })).toBeInTheDocument();
      expect(screen.getByText('needs review')).toBeInTheDocument();
      expect(screen.getByText('JOB-0005').closest('a')).toBe(card);
      await switchLocale('zh-CN');
      expect(screen.getByText('JOB-0005').closest('a')).toBe(card);
      expect(screen.getByText('1 个作业需要你处理')).toBeInTheDocument();
    });
    expect(requests).toEqual([]);
  });

  test('empty and error states are localized', async () => {
    stubList({ jobs: [] });
    const { unmount } = mount(`/orgs/${SLUG}/jobs`, 'zh-CN');
    expect(await screen.findByText('暂无作业。')).toBeInTheDocument();
    expect(screen.getByText('队列已清空 · 没有等你处理的事项')).toBeInTheDocument();
    unmount();

    stubList({ error: true });
    mount(`/orgs/${SLUG}/jobs`, 'zh-CN');
    expect(await screen.findByText('无法加载作业。')).toBeInTheDocument();
  });
});

describe('Job detail i18n', () => {
  test.each(['en', 'zh-CN'] as const)('C8 %s populated cascade preserves its query, raw task and localized waiting', async (locale) => {
    stubDetail(JOB);
    const queries: string[] = [];
    server.use(http.get(`/api/v1/orgs/${SLUG}/dashboard/summary`, () => HttpResponse.json({})));
    server.use(http.get(`/api/v1/orgs/${SLUG}/tasks`, ({ request }) => {
      queries.push(new URL(request.url).searchParams.get('blocked_on_job_id') ?? '');
      return HttpResponse.json({ tasks: [{
        task_id: 'TASK-CASE-J', status: 'in_progress', block_kind: 'blocked_on_job',
        brief: 'Raw cascade task / 原文',
      }], next_cursor: null });
    }));
    mount(`/orgs/${SLUG}/jobs/${JOB.id}`, locale);
    const link = await screen.findByRole('link', { name: 'TASK-CASE-J' });
    const row = link.closest('li')!;
    expect(row).toHaveTextContent(locale === 'en' ? '· waiting on jobs' : '· 等待作业');
    expect(row).toHaveTextContent('in_progress');
    expect(row).toHaveTextContent('Raw cascade task / 原文');
    expect(link).toHaveAttribute('href', `/orgs/${SLUG}/tasks/TASK-CASE-J`);
    expect(queries).toEqual([JOB.id]);
  });

  test.each(['en', 'zh-CN'] as const)('C8 %s cascade loading, empty and error states use owned copy', async (locale) => {
    stubDetail(JOB);
    let release!: () => void;
    let fail = false;
    const pending = new Promise<void>((resolve) => { release = resolve; });
    server.use(
      http.get(`/api/v1/orgs/${SLUG}/dashboard/summary`, () => HttpResponse.json({})),
      http.get(`/api/v1/orgs/${SLUG}/tasks`, async () => {
        await pending;
        return fail ? HttpResponse.json({ detail: 'raw fixture failure' }, { status: 500 })
          : HttpResponse.json({ tasks: [], next_cursor: null });
      }),
    );
    const view = mount(`/orgs/${SLUG}/jobs/${JOB.id}`, locale);
    const heading = await screen.findByRole('heading', { name: locale === 'en' ? 'If approved' : '若批准' });
    expect(heading.closest('section')).toHaveTextContent(locale === 'en' ? 'Loading blocked tasks…' : '正在加载被阻塞的任务…');
    await act(async () => { release(); });
    expect(await screen.findByText(locale === 'en' ? 'No tasks are currently blocked on this job.' : '当前没有任务被此作业阻塞。')).toBeInTheDocument();
    view.unmount();
    fail = true;
    mount(`/orgs/${SLUG}/jobs/${JOB.id}`, locale);
    expect(await screen.findByText(locale === 'en' ? 'Could not load blocked tasks.' : '无法加载被阻塞的任务。')).toBeInTheDocument();
  });

  test('pending gated job: zh-CN chrome, verbatim values, same nodes and zero requests across switches', async () => {
    stubDetail(JOB);
    mount(`/orgs/${SLUG}/jobs/JOB-0001`, 'zh-CN');

    expect(await screen.findByRole('heading', { name: 'Clean up stale Docker images' })).toBeInTheDocument();
    expect(screen.getByRole('link', { name: '← 返回 TASK-0042' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '拒绝' })).toBeInTheDocument();
    const approve = screen.getByRole('button', { name: '批准并运行' });
    expect(screen.getByText('原样命令 · 将完全按此运行')).toBeInTheDocument();
    expect(screen.getByText('命令')).toBeInTheDocument();
    expect(await screen.findByText('当前没有任务被此作业阻塞。')).toBeInTheDocument();
    expect(screen.getByRole('heading', { name: '若批准' })).toBeInTheDocument();
    expect(screen.getByText('已标记待审核')).toBeInTheDocument();
    expect(screen.getByText('此操作需要把关 — 请使用上方的“批准并运行”。')).toBeInTheDocument();
    const rail = screen.getByRole('complementary');
    for (const label of ['请求者', '任务', '创建时间', '执行', '解释器', '最长运行时间', '持久运行', '需要审核']) {
      expect(within(rail).getByText(label)).toBeInTheDocument();
    }
    expect(within(rail).getByText('300 秒')).toBeInTheDocument();
    expect(within(rail).getAllByText('否').length).toBe(1);
    expect(within(rail).getByText('是')).toBeInTheDocument();
    // Daemon values verbatim.
    expect(screen.getByText('Disk usage is above 90% on the build server.')).toBeInTheDocument();
    expect(screen.getByText('docker image prune -af')).toBeInTheDocument();
    expect(within(rail).getByText('engineering_head')).toBeInTheDocument();
    expect(within(rail).getByText('bash')).toBeInTheDocument();
    expect(screen.getByText('pending')).toBeInTheDocument();

    const requests = await countRequests(async () => {
      await switchLocale('en');
      expect(screen.getByRole('button', { name: 'Approve & run' })).toBe(approve);
      expect(screen.getByText('Verbatim command · runs exactly this')).toBeInTheDocument();
      expect(screen.getByRole('heading', { name: 'If approved' })).toBeInTheDocument();
      expect(within(rail).getByText('300s')).toBeInTheDocument();
      expect(screen.getByRole('link', { name: '← Back to TASK-0042' })).toBeInTheDocument();
      await switchLocale('zh-CN');
      expect(screen.getByRole('button', { name: '批准并运行' })).toBe(approve);
    });
    expect(requests).toEqual([]);
  });

  test('completed job: localized rail and output chrome; stdout and exit code verbatim', async () => {
    stubDetail(
      job({
        status: 'completed',
        exit_code: 0,
        duration_ms: 5000,
        finished_at: '2026-05-23T12:05:00Z',
        reviewed_by: 'founder',
      }),
    );
    mount(`/orgs/${SLUG}/jobs/JOB-0001`, 'zh-CN');
    expect(await screen.findByText('Deleted 3 images')).toBeInTheDocument();
    expect(screen.getByRole('heading', { name: '输出' })).toBeInTheDocument();
    expect(screen.getByText('（空）')).toBeInTheDocument();
    expect(screen.getByText('stdout')).toBeInTheDocument();
    const rail = screen.getByRole('complementary');
    expect(within(rail).getByText('退出码')).toBeInTheDocument();
    expect(within(rail).getByText('0')).toBeInTheDocument();
    expect(within(rail).getByText('耗时')).toBeInTheDocument();
    expect(within(rail).getByText('5.0 秒')).toBeInTheDocument();
    expect(within(rail).getByText('审核者')).toBeInTheDocument();
    expect(within(rail).getByText('founder')).toBeInTheDocument();
  });

  test('not-found and load-error states are localized with the job id verbatim', async () => {
    server.use(
      http.get(`/api/v1/orgs/${SLUG}/jobs/JOB-0404`, () =>
        HttpResponse.json({ detail: { code: 'unknown_job' } }, { status: 404 }),
      ),
    );
    mount(`/orgs/${SLUG}/jobs/JOB-0404`, 'zh-CN');
    expect(await screen.findByText('无法加载 JOB-0404。')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '重试' })).toBeInTheDocument();
  });
});

describe('Job dialogs i18n', () => {
  test('Run dialog: zh-CN chrome; the typed draft keeps node, value and focus across switches with zero requests', async () => {
    stubDetail(job({ cwd_hint: 'repos/app' }));
    mount(`/orgs/${SLUG}/jobs/JOB-0001`, 'zh-CN');
    const user = userEvent.setup();
    await user.click(await screen.findByRole('button', { name: '批准并运行' }));

    const cwd = await screen.findByLabelText('覆盖工作目录');
    const dialog = dialogOwning(cwd);
    const close = within(dialog).getByRole('button', { name: '关闭' });
    expect(within(dialog).getByRole('heading', { name: '批准并运行 JOB-0001' })).toBeInTheDocument();
    expect(within(dialog).getByText('（bash · cwd 提示：repos/app）')).toBeInTheDocument();
    expect(within(dialog).getByLabelText('超时（秒）')).toHaveValue(300);
    expect(within(dialog).getByRole('button', { name: '取消' })).toBeInTheDocument();
    await user.type(cwd, 'repos/other');

    const requests = await countRequests(async () => {
      await switchLocale('en');
      expect(within(dialog).getByRole('heading', { name: 'Approve & run JOB-0001' })).toBeInTheDocument();
      expect(screen.getByLabelText('Working directory override')).toBe(cwd);
      expect(within(dialog).getByRole('button', { name: 'Close' })).toBe(close);
      await switchLocale('zh-CN');
      expect(within(dialog).getByRole('button', { name: '关闭' })).toBe(close);
    });
    expect(requests).toEqual([]);
    expect(screen.getByLabelText('覆盖工作目录')).toBe(cwd);
    expect(cwd).toHaveValue('repos/other');
    expect(document.activeElement).toBe(cwd);
  });

  type Case = { name: string; status: number; body: Record<string, unknown>; zh: string; en: string };
  const RUN_CASES: Case[] = [
    {
      name: 'recognized code maps to catalog copy',
      status: 409,
      body: { detail: { code: 'not_pending', status: 'running' } },
      zh: '此作业已不再处于待处理状态。',
      en: 'This job is no longer pending.',
    },
    {
      name: 'unknown code stays raw',
      status: 409,
      body: { detail: { code: 'runner_quota_exhausted_7' } },
      zh: 'runner_quota_exhausted_7',
      en: 'runner_quota_exhausted_7',
    },
    {
      name: 'code-less string detail stays verbatim',
      status: 400,
      body: { detail: 'cwd_override escapes the agent workspace' },
      zh: 'cwd_override escapes the agent workspace',
      en: 'cwd_override escapes the agent workspace',
    },
    {
      name: 'empty code with a non-string detail falls back',
      status: 500,
      body: { detail: { code: '' } },
      zh: '运行失败。',
      en: 'Run failed.',
    },
    {
      name: 'blank string detail falls back',
      status: 500,
      body: { detail: '   ' },
      zh: '运行失败。',
      en: 'Run failed.',
    },
  ];

  test.each(RUN_CASES)('Run dialog diagnostic boundary: $name', async ({ status, body, zh, en }) => {
    stubDetail(JOB);
    server.use(
      http.post(`/api/v1/orgs/${SLUG}/jobs/JOB-0001/run`, () => HttpResponse.json(body, { status })),
    );
    mount(`/orgs/${SLUG}/jobs/JOB-0001`, 'zh-CN');
    const user = userEvent.setup();
    await user.click(await screen.findByRole('button', { name: '批准并运行' }));
    const cwd = await screen.findByLabelText('覆盖工作目录');
    const dialog = dialogOwning(cwd);
    await user.click(within(dialog).getByRole('button', { name: '批准并运行' }));

    const error = await within(dialog).findByText(zh);
    expect(error.textContent).toBe(zh);
    expect(within(dialog).queryByText(/API \d{3}/)).not.toBeInTheDocument();
    await switchLocale('en');
    expect(within(dialog).getByText(en)).toBe(error);
  });

  test('Reject dialog: zh-CN chrome, draft kept across switches, mapped daemon code re-translates in place', async () => {
    stubDetail(JOB);
    server.use(
      http.post(`/api/v1/orgs/${SLUG}/jobs/JOB-0001/reject`, () =>
        HttpResponse.json({ detail: { code: 'reason_too_long', max: 1000 } }, { status: 422 }),
      ),
    );
    mount(`/orgs/${SLUG}/jobs/JOB-0001`, 'zh-CN');
    const user = userEvent.setup();
    await user.click(await screen.findByRole('button', { name: '拒绝' }));
    const reason = await screen.findByLabelText('原因');
    const dialog = dialogOwning(reason);
    const close = within(dialog).getByRole('button', { name: '关闭' });
    expect(within(dialog).getByRole('heading', { name: '拒绝 JOB-0001' })).toBeInTheDocument();
    expect(reason).toHaveAttribute('placeholder', '原因（必填，最多 1000 个字符）');
    await user.type(reason, 'Too risky');

    const requests = await countRequests(async () => {
      await switchLocale('en');
      expect(screen.getByLabelText('Reason')).toBe(reason);
      expect(within(dialog).getByRole('button', { name: 'Close' })).toBe(close);
      await switchLocale('zh-CN');
      expect(within(dialog).getByRole('button', { name: '关闭' })).toBe(close);
    });
    expect(requests).toEqual([]);
    expect(reason).toHaveValue('Too risky');
    expect(document.activeElement).toBe(reason);

    await user.click(within(dialog).getByRole('button', { name: '拒绝' }));
    expect(await within(dialog).findByText('原因不能超过 1000 个字符。')).toBeInTheDocument();
    await switchLocale('en');
    expect(within(dialog).getByText('Reason must be 1000 characters or fewer.')).toBeInTheDocument();
  });

  test('Stop on a running job: a recognized daemon code is localized, not "API 409"', async () => {
    stubDetail(job({ status: 'running', started_at: '2026-05-23T12:00:00Z' }));
    server.use(
      http.post(`/api/v1/orgs/${SLUG}/jobs/JOB-0001/stop`, () =>
        HttpResponse.json({ detail: { code: 'not_running', status: 'completed' } }, { status: 409 }),
      ),
    );
    mount(`/orgs/${SLUG}/jobs/JOB-0001`, 'zh-CN');
    const user = userEvent.setup();
    expect(await screen.findByText('正在等待输出…')).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: '停止' }));
    expect(await screen.findByText('此作业未在运行。')).toBeInTheDocument();
    await switchLocale('en');
    await waitFor(() => expect(screen.getByText('This job is not running.')).toBeInTheDocument());
  });
});
