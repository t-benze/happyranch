import { fireEvent, screen, waitFor } from '@testing-library/react';
import { http, HttpResponse } from 'msw';
import { beforeEach, describe, expect, test } from 'vitest';
import { renderWithProviders, savedLocaleAdapter, LocaleTestSwitch } from '@/test/render';
import { server } from '@/test/server';
import type { TaskPauseProjection, TaskRecord } from '@/lib/api/types';
import { AppRoutes } from '@/routes';

beforeEach(() => { sessionStorage.setItem('happyranch.token', 'test-token'); });

const projection = (changes: Partial<TaskPauseProjection> = {}): TaskPauseProjection => ({
  org_slug: 'alpha', root_task_id: 'ROOT-1', lifecycle_status: 'in_progress',
  held: false, effective_hold: false, generation: 6, control_state: 'unheld',
  paused_at: null, resumed_at: null, blockers: [], evidence_complete: true,
  observed_at: '2026-10-11T00:00:00Z', ...changes,
});
const record = (changes: Partial<TaskRecord> = {}): TaskRecord => ({
  task_id: 'ROOT-1', team: 'engineering', brief: 'Preserved authored brief',
  status: 'in_progress', block_kind: 'delegated', parent_task_id: null,
  revisit_of_task_id: null, created_at: '2026-10-11T00:00:00Z', updated_at: '2026-10-11T00:00:00Z',
  closed_at: null, cancelled_at: null, session_timeout_seconds: null,
  pause: projection(), ...changes,
} as TaskRecord);
// THR-292 seq45: all pause UI lives on Task detail only, so every case mounts the real detail route.
// `current()` is re-read per request so a mutation can change the served projection.
function stubDetail(current: () => TaskRecord, reads: { details: number } = { details: 0 }) {
  server.use(
    http.get('/api/v1/orgs', () => HttpResponse.json({ orgs: [{ slug: 'alpha', root: '/isolated-fixture' }] })),
    http.get('/api/v1/orgs/alpha/dashboard/summary', () => HttpResponse.json({ org_age_days: 1 })),
    http.get('/api/v1/orgs/alpha/jobs/', () => HttpResponse.json({ jobs: [], next_cursor: null })),
    http.get('/api/v1/orgs/alpha/tasks/:id', ({ params }) => {
      const task = current();
      if (params.id !== task.task_id) return HttpResponse.json({ detail: 'not_found' }, { status: 404 });
      reads.details++;
      return HttpResponse.json({ task, pause: task.pause, results: [], audit_log: [], revisit_chain: [], direct_revisits: [], predecessor_prior_status: null });
    }),
    http.get('/api/v1/orgs/alpha/tasks/:id/recall', ({ params }) => HttpResponse.json({
      task_id: params.id, assigned_agent: 'agent-a', brief: current().brief, status: current().status, output_summary: null, children: [],
    })),
    http.get('/api/v1/orgs/alpha/tasks/:id/events', () => HttpResponse.text('', { headers: { 'content-type': 'text/event-stream' } })),
  );
  return reads;
}
function mountDetail(task = record(), locale: 'en' | 'zh-CN' = 'en') {
  const reads = stubDetail(() => task);
  const ui = renderWithProviders(<><AppRoutes /><LocaleTestSwitch to="zh-CN" /><LocaleTestSwitch to="en" /></>, {
    route: `/orgs/alpha/tasks/${task.task_id}`, i18n: { adapter: savedLocaleAdapter(locale) },
  });
  return { ...ui, reads };
}
const controls = async (id = 'ROOT-1') => {
  await waitFor(() => expect(document.querySelector(`[data-task-pause="${id}"]`)).not.toBeNull());
  return document.querySelector(`[data-task-pause="${id}"]`) as HTMLElement;
};

describe('P18a root Pause UI and shipping transport on Task detail', () => {
  test('parked root sends displayed generation once; pending submission disables repeats', async () => {
    const requests: unknown[] = [];
    let release!: () => void;
    const ready = new Promise<void>(resolve => { release = resolve; });
    server.use(http.post('/api/v1/orgs/alpha/tasks/ROOT-1/pause', async ({ request }) => {
      requests.push(await request.json()); await ready;
      return HttpResponse.json({ changed: true, pause: projection({ held: true, generation: 7, control_state: 'paused' }) });
    }));
    mountDetail();
    const pause = await screen.findByRole('button', { name: 'Pause' });
    fireEvent.click(pause); fireEvent.click(pause);
    await waitFor(() => expect(requests).toEqual([{ expected_generation: 6 }]));
    expect(pause).toBeDisabled();
    release();
    await waitFor(() => expect(pause).not.toBeDisabled());
    expect(requests).toEqual([{ expected_generation: 6 }]);
  });
});

const controlsCases = [
  ['root', {}, 'Pause'],
  ['pending', { status: 'pending' }, null],
  ['escalated unheld', { status: 'escalated' }, null],
  ['escalated held', { status: 'escalated', pause: projection({ held: true, effective_hold: true, control_state: 'paused' }) }, 'Resume'],
  ['pausing', { pause: projection({ held: true, effective_hold: true, control_state: 'pausing' }) }, 'Resume'],
  ['paused', { pause: projection({ held: true, effective_hold: true, control_state: 'paused' }) }, 'Resume'],
  ['child', { task_id: 'CHILD-9', parent_task_id: 'PARENT-8', pause: projection({ held: true, effective_hold: true, control_state: 'pausing' }) }, null],
  ['missing parent is not a root', { parent_task_id: undefined }, null],
  ['terminal held', { status: 'failed', pause: projection({ held: true, effective_hold: false }) }, null],
  ['completed', { status: 'completed' }, null],
  ['cancelled', { status: 'cancelled' }, null],
  ['superseded', { status: 'superseded' }, null],
  ['missing projection', { pause: undefined }, null],
  ['missing generation', { pause: { ...projection(), generation: undefined } }, null],
  ['unsafe generation', { pause: projection({ generation: Number.MAX_SAFE_INTEGER + 1 }) }, null],
  ['wrong org projection', { pause: projection({ org_slug: 'beta' }) }, null],
  ['wrong root projection', { pause: projection({ root_task_id: 'WRONG' }) }, null],
] as const;

test.each(controlsCases)('P18a detail eligibility: %s', async (_name, changes, action) => {
  const task = record(changes as Partial<TaskRecord>);
  mountDetail(task);
  // Every case renders the pause region (or its unavailable copy) before the action assertion.
  await waitFor(() => expect(document.querySelector(`[data-task-pause="${task.task_id}"]`) ?? screen.queryByText(/Pause controls unavailable/)).not.toBeNull());
  expect(screen.queryByRole('button', { name: 'Pause' })).toBe(action === 'Pause' ? screen.getByRole('button', { name: 'Pause' }) : null);
  expect(screen.queryByRole('button', { name: 'Resume' })).toBe(action === 'Resume' ? screen.getByRole('button', { name: 'Resume' }) : null);
});

test.each(['en', 'zh-CN'] as const)('P18b detail typed terminal blockers and actual child root link: %s', async locale => {
  const blockers = (['task_session', 'job', 'launch', 'result_processing', 'unknown'] as const).map(kind => ({
    kind, task_id: `RAW-${kind}`, job_id: kind === 'job' ? 'JOB-9' : null,
    owner_kind: 'raw-owner', reason: 'raw_reason', observed_at: '2026-10-11T00:00:00Z',
  }));
  mountDetail(record({ task_id: 'CHILD-9', parent_task_id: 'PARENT-8', status: 'failed',
    pause: projection({ held: true, effective_hold: false, blockers, evidence_complete: false }) }), locale);
  const region = await controls('CHILD-9');
  expect(region.querySelector('a[href="/orgs/alpha/tasks/ROOT-1"]')).toHaveTextContent('ROOT-1');
  expect(region).toHaveTextContent(locale === 'en' ? /Task ended; unresolved/ : /任务已结束，仍有/);
  for (const kind of ['task_session', 'job', 'launch', 'result_processing', 'unknown']) expect(region.textContent).toContain(`RAW-${kind}`);
  expect(region).toHaveTextContent(locale === 'en' ? 'Unknown execution evidence' : '未知执行证据');
  expect(screen.queryByRole('button', { name: /^(Pause|Resume|暂停|恢复)$/ })).toBeNull();
});

test.each(['en', 'zh-CN'] as const)('P18b inherited child detail explains the held root with a link and no child action: %s', async locale => {
  mountDetail(record({ task_id: 'CHILD-9', parent_task_id: 'ROOT-1', pause: projection({ held: true, effective_hold: true, control_state: 'paused' }) }), locale);
  const region = await controls('CHILD-9');
  expect(region).toHaveTextContent(locale === 'en' ? /Hold inherited from root ROOT-1/ : /继承根任务/);
  expect(region.querySelector('a[href="/orgs/alpha/tasks/ROOT-1"]')).toHaveTextContent('ROOT-1');
  expect(screen.queryByRole('button', { name: /^(Pause|Resume|暂停|恢复)$/ })).toBeNull();
});

test.each(['pausing', 'paused'] as const)('P18a explicit detail Resume during %s sends one request and no Pause', async control_state => {
  const calls: string[] = [];
  server.use(http.post('/api/v1/orgs/alpha/tasks/ROOT-1/:action', async ({ request, params }) => {
    calls.push(String(params.action));
    expect(await request.json()).toEqual({ expected_generation: 7 });
    return HttpResponse.json({ changed: true, pause: projection({ generation: 8 }) });
  }));
  mountDetail(record({ pause: projection({ generation: 7, held: true, effective_hold: true, control_state }) }));
  fireEvent.click(await screen.findByRole('button', { name: 'Resume' }));
  await waitFor(() => expect(calls).toEqual(['resume']));
});

const errorCases = [
  [409, 'control_generation_conflict', 'Pause state changed.', '暂停状态已变更。'],
  [409, 'root_task_required', 'Only the actual root', '只能暂停或恢复实际根任务。'],
  [409, 'task_not_in_progress', 'Pause requires', '只有进行中的根任务才能暂停。'],
  [409, 'task_terminal', 'This task has ended', '此任务已结束，无法暂停或恢复。'],
  [409, 'pause_control_unavailable', 'Pause control evidence', '暂停控制证据不可用。'],
  [403, 'forbidden', 'You do not have permission', '你没有执行此操作的权限。'],
  [418, 'RAW_FUTURE_CODE', 'RAW_FUTURE_CODE', 'RAW_FUTURE_CODE'],
] as const;

test.each(errorCases)('P18a detail refusal %s/%s is accessible and retranslates without retry', async (status, code, english, chinese) => {
  let writes = 0;
  server.use(http.post('/api/v1/orgs/alpha/tasks/ROOT-1/pause', () => {
    writes++; return HttpResponse.json({ detail: { code } }, { status });
  }));
  const region = await (mountDetail(), controls());
  fireEvent.click(screen.getByRole('button', { name: 'Pause' }));
  await waitFor(() => expect(region.querySelector('[role="alert"]')).toHaveTextContent(english));
  fireEvent.click(screen.getByTestId('test-set-locale-zh-CN'));
  expect(region.querySelector('[role="alert"]')).toHaveTextContent(chinese);
  expect(writes).toBe(1);
});

test('P18a detail bootstrap authentication refusal is translated and does not invent success', async () => {
  let writes = 0;
  server.use(
    http.post('/api/v1/orgs/alpha/tasks/ROOT-1/pause', () => { writes++; return new HttpResponse(null, { status: 401 }); }),
    http.get('/api/v1/auth/bootstrap', () => new HttpResponse(null, { status: 401 })),
  );
  const region = await (mountDetail(), controls());
  fireEvent.click(screen.getByRole('button', { name: 'Pause' }));
  await waitFor(() => expect(region.querySelector('[role="alert"]')).toHaveTextContent('Authentication failed.'));
  fireEvent.click(screen.getByTestId('test-set-locale-zh-CN'));
  expect(region.querySelector('[role="alert"]')).toHaveTextContent('身份验证失败。');
  expect(writes).toBe(1);
});

test('P18c mounted detail conflict refreshes without retry or overview read; deliberate Resume uses fresh generation', async () => {
  let current = projection();
  let overviews = 0;
  const writes: { action: string; body: unknown }[] = [];
  const reads = stubDetail(() => record({ pause: current }));
  server.use(
    // THR-292 seq41/45: no web surface reads the overview; count any read as a regression.
    http.get('/api/v1/orgs/alpha/tasks/pause-overview', () => { overviews++; return HttpResponse.json({ org_slug: 'alpha', observed_at: '2026-10-11T00:00:00Z', evidence_complete: true, counts: null, next_cursor: null, unheld_runnable: [], pausing: [], paused: [], terminal_drain: [], unavailable_roots: [] }); }),
    http.post('/api/v1/orgs/alpha/tasks/ROOT-1/:action', async ({ request, params }) => {
      writes.push({ action: String(params.action), body: await request.json() });
      if (params.action === 'pause') {
        current = projection({ held: true, effective_hold: true, control_state: 'paused', generation: 9 });
        return HttpResponse.json({ detail: { code: 'control_generation_conflict' } }, { status: 409 });
      }
      current = projection({ generation: 10 });
      return HttpResponse.json({ changed: true, pause: current });
    }),
  );
  renderWithProviders(<AppRoutes />, { route: '/orgs/alpha/tasks/ROOT-1' });
  const region = await controls();
  await screen.findByRole('button', { name: 'Pause' });
  const previous = reads.details;
  fireEvent.click(screen.getByRole('button', { name: 'Pause' }));
  await waitFor(() => expect(region.querySelector('[role="alert"]')).toHaveTextContent('Pause state changed.'));
  await waitFor(() => expect(screen.getAllByRole('button', { name: 'Resume' })).toHaveLength(1));
  expect(reads.details).toBeGreaterThan(previous);
  expect(writes).toEqual([{ action: 'pause', body: { expected_generation: 6 } }]);
  fireEvent.click(screen.getByRole('button', { name: 'Resume' }));
  await waitFor(() => expect(screen.getAllByRole('button', { name: 'Pause' })).toHaveLength(1));
  expect(writes).toEqual([{ action: 'pause', body: { expected_generation: 6 } }, { action: 'resume', body: { expected_generation: 9 } }]);
  expect(overviews).toBe(0);
});


test('P18b detail unavailable generation retains terminal drain diagnostics', async () => {
  const unknown = { kind: 'unknown' as const, task_id: 'ROOT-1', job_id: null, owner_kind: 'host', reason: 'uncertain_tree', observed_at: '2026-10-11T00:00:00Z' };
  mountDetail(record({ status: 'failed', pause: { ...projection({ held: true, blockers: [unknown], evidence_complete: false }), generation: undefined } as unknown as TaskPauseProjection }));
  expect(await screen.findByText(/Pause controls unavailable/)).toBeInTheDocument();
  expect(screen.getByText(/Task ended; unresolved/)).toBeInTheDocument();
  expect(screen.getByText('Unknown execution evidence')).toBeInTheDocument();
  expect(screen.queryByRole('button', { name: 'Resume' })).toBeNull();
});

// THR-292 seq77 task-detail header: compact Pause in the shared action row, hold indicator, collapsed drain disclosure.
const blocker = (kind: TaskPauseProjection['blockers'][number]['kind'], task_id: string, job_id: string | null = null) => ({
  kind, task_id, job_id, owner_kind: 'raw-owner', reason: 'raw_reason', observed_at: '2026-10-11T00:00:00Z',
});
const headerRow = (region: HTMLElement) => region.parentElement as HTMLElement;

test('seq77 normal root: compact Pause shares the ordinary action row with Revisit/Cancel/audit and adds no hold clutter', async () => {
  mountDetail(record({ block_kind: null }));
  const region = await controls();
  const pause = await screen.findByRole('button', { name: 'Pause' });
  const row = headerRow(region);
  for (const name of ['Revisit', 'Cancel']) expect(row.contains(screen.getByRole('button', { name }))).toBe(true);
  expect(row.querySelector('a[href="/orgs/alpha/audit?task_id=ROOT-1"]')).not.toBeNull();
  expect(region.contains(pause)).toBe(true);
  expect(row.textContent).not.toMatch(/Not held|Paused|Drain evidence|Observed|still running|Waiting for current work/);
  expect(region.textContent).toBe('Pause');
  expect(row.querySelector('details')).toBeNull();
});

test.each(['en', 'zh-CN'] as const)('seq77 pausing root: amber waiting indicator, secondary Resume, collapsed disclosure with real task/job links: %s', async locale => {
  mountDetail(record({ pause: projection({ held: true, effective_hold: true, control_state: 'pausing', evidence_complete: true,
    blockers: [blocker('job', 'ROOT-1', 'JOB-9'), blocker('task_session', 'CHILD-2')] }) }), locale);
  const region = await controls();
  const resume = await screen.findByRole('button', { name: locale === 'en' ? 'Resume' : '恢复' });
  expect(resume.className).not.toMatch(/\bbg-primary\b/);
  expect(region).toHaveTextContent(locale === 'en' ? 'Waiting for current work to finish' : '正在等待当前工作完成');
  const details = region.querySelector('details') as HTMLDetailsElement;
  expect(details.open).toBe(false);
  expect(details.querySelector('summary')).toHaveTextContent(locale === 'en' ? /^2 still running$/ : /^2 项仍在运行$/);
  fireEvent.click(details.querySelector('summary') as HTMLElement);
  expect(details.open).toBe(true);
  expect(details.querySelector('a[href="/orgs/alpha/jobs/JOB-9"]')).toHaveTextContent('JOB-9');
  expect(details.querySelector('a[href="/orgs/alpha/tasks/CHILD-2"]')).toHaveTextContent('CHILD-2');
  expect(details.querySelector('a[href="/orgs/alpha/tasks/ROOT-1"]')).toHaveTextContent('ROOT-1');
  expect(details).toHaveTextContent(locale === 'en' ? 'Nothing new will start.' : '不会启动新工作。');
  expect(details.textContent).not.toMatch(/started|agent-a/);
});

test('seq77 launch/result/unknown and incomplete evidence are never counted as running', async () => {
  mountDetail(record({ pause: projection({ held: true, effective_hold: true, control_state: 'pausing', evidence_complete: false,
    blockers: [blocker('launch', 'ROOT-1'), blocker('result_processing', 'ROOT-1'), blocker('unknown', 'ROOT-1')] }) }));
  const region = await controls();
  const summary = region.querySelector('details summary') as HTMLElement;
  expect(summary).toHaveTextContent(/^3 other items to resolve · Drain evidence incomplete$/);
  expect(summary.textContent).not.toMatch(/running/);
  for (const label of ['Launch', 'Result processing', 'Unknown execution evidence', 'Drain evidence incomplete; unknown work may remain.'])
    expect(region).toHaveTextContent(label);
  expect(screen.getByRole('button', { name: 'Resume' })).toBeInTheDocument();
});

test('seq77 paused root: distinct Paused indicator, primary Resume, no disclosure when drain evidence is complete', async () => {
  mountDetail(record({ pause: projection({ held: true, effective_hold: true, control_state: 'paused' }) }));
  const region = await controls();
  const resume = await screen.findByRole('button', { name: 'Resume' });
  expect(resume.className).toMatch(/\bbg-primary\b/);
  expect(region).toHaveTextContent('Paused');
  expect(region).not.toHaveTextContent('Waiting for current work');
  expect(region.querySelector('details')).toBeNull();
  expect(screen.queryByRole('button', { name: 'Pause' })).toBeNull();
});

test.each(['pausing', 'paused'] as const)('seq77 Resume during %s: pending protection and a refused write keeps the held state', async control_state => {
  const bodies: unknown[] = [];
  let release!: () => void;
  const ready = new Promise<void>(resolve => { release = resolve; });
  server.use(http.post('/api/v1/orgs/alpha/tasks/ROOT-1/resume', async ({ request }) => {
    bodies.push(await request.json()); await ready;
    return HttpResponse.json({ detail: { code: 'control_generation_conflict' } }, { status: 409 });
  }));
  mountDetail(record({ pause: projection({ generation: 7, held: true, effective_hold: true, control_state,
    blockers: control_state === 'pausing' ? [blocker('job', 'ROOT-1', 'JOB-9')] : [] }) }));
  const region = await controls();
  const resume = await screen.findByRole('button', { name: 'Resume' });
  fireEvent.click(resume); fireEvent.click(resume);
  await waitFor(() => expect(bodies).toEqual([{ expected_generation: 7 }]));
  expect(resume).toBeDisabled();
  release();
  await waitFor(() => expect(region.querySelector('[role="alert"]')).toHaveTextContent('Pause state changed.'));
  expect(bodies).toEqual([{ expected_generation: 7 }]);
  expect(region).toHaveTextContent(control_state === 'paused' ? 'Paused' : 'Waiting for current work to finish');
  expect(screen.queryByRole('button', { name: 'Pause' })).toBeNull();
});

test.each(['en','zh-CN'] as const)('B01 unknown job cleanup remains unconfirmed with raw reasons %s', async locale => {
  mountDetail(record({pause:projection({held:true,effective_hold:true,control_state:'pausing',evidence_complete:false,
    blockers:[{...blocker('unknown','ROOT-1','JOB-9'),owner_kind:'job'}]})}),locale);
  const region = await controls();
  expect(region.querySelector('summary')).toHaveTextContent(locale === 'en' ? '1 job cleanup unconfirmed' : '作业清理尚未确认 1 项');
  expect(region).toHaveTextContent(locale === 'en' ? 'Job descendant cleanup unconfirmed' : '作业子进程清理尚未确认');
  expect(region).toHaveTextContent(locale === 'en' ? 'The job record may be terminal' : '作业记录可能已结束');
  expect(region.querySelector('summary')?.textContent).not.toMatch(/still running|仍在运行/);
  expect(region.querySelector('a[href="/orgs/alpha/jobs/JOB-9"]')).toHaveTextContent('JOB-9');
});
