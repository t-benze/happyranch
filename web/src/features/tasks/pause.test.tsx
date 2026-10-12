import { fireEvent, screen, waitFor, within } from '@testing-library/react';
import { http, HttpResponse } from 'msw';
import { beforeEach, describe, expect, test } from 'vitest';
import { Route, Routes } from 'react-router-dom';
import { renderWithProviders, savedLocaleAdapter, LocaleTestSwitch } from '@/test/render';
import { server } from '@/test/server';
import type { TaskPauseProjection, TaskRecord } from '@/lib/api/types';
import { OrgProvider } from '@/lib/orgSlug';
import { AppRoutes } from '@/routes';
import { TaskListRow } from './TaskListRow';

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
function mountRow(task = record(), locale: 'en' | 'zh-CN' = 'en') {
  return renderWithProviders(<><Routes><Route path="/orgs/:slug/tasks" element={
    <OrgProvider><TaskListRow task={task} to={`/orgs/alpha/tasks/${task.task_id}`} taskRoutes={{ detail: id => `/orgs/alpha/tasks/${id}` }} /></OrgProvider>
  } /></Routes><LocaleTestSwitch to="zh-CN" /><LocaleTestSwitch to="en" /></>, {
    route: '/orgs/alpha/tasks', i18n: { adapter: savedLocaleAdapter(locale) },
  });
}

describe('P18a root Pause UI and shipping transport', () => {
  test('parked root sends displayed generation once; pending submission disables repeats', async () => {
    const requests: unknown[] = [];
    let release!: () => void;
    const ready = new Promise<void>(resolve => { release = resolve; });
    server.use(http.post('/api/v1/orgs/alpha/tasks/ROOT-1/pause', async ({ request }) => {
      requests.push(await request.json()); await ready;
      return HttpResponse.json({ changed: true, pause: projection({ held: true, generation: 7, control_state: 'paused' }) });
    }));
    mountRow();
    const pause = screen.getByRole('button', { name: 'Pause' });
    fireEvent.click(pause); fireEvent.click(pause);
    await waitFor(() => expect(requests).toEqual([{ expected_generation: 6 }]));
    expect(pause).toBeDisabled();
    release();
    await waitFor(() => expect(pause).not.toBeDisabled());
    expect(screen.getByRole('link', { name: /Preserved authored brief/ })).toHaveAttribute('href', '/orgs/alpha/tasks/ROOT-1');
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

test.each(controlsCases)('P18a eligibility: %s', (_name, changes, action) => {
  mountRow(record(changes as Partial<TaskRecord>));
  expect(screen.queryByRole('button', { name: 'Pause' })).toBe(action === 'Pause' ? screen.getByRole('button', { name: 'Pause' }) : null);
  expect(screen.queryByRole('button', { name: 'Resume' })).toBe(action === 'Resume' ? screen.getByRole('button', { name: 'Resume' }) : null);
});

test.each(['en', 'zh-CN'] as const)('P18b typed terminal blockers and actual child root link: %s', locale => {
  const blockers = (['task_session', 'job', 'launch', 'result_processing', 'unknown'] as const).map(kind => ({
    kind, task_id: `RAW-${kind}`, job_id: kind === 'job' ? 'JOB-9' : null,
    owner_kind: 'raw-owner', reason: 'raw_reason', observed_at: '2026-10-11T00:00:00Z',
  }));
  const { container } = mountRow(record({ task_id: 'CHILD-9', parent_task_id: 'PARENT-8', status: 'failed',
    pause: projection({ held: true, effective_hold: false, blockers, evidence_complete: false }) }), locale);
  expect(screen.getByRole('link', { name: 'ROOT-1' })).toHaveAttribute('href', '/orgs/alpha/tasks/ROOT-1');
  expect(screen.getByText(locale === 'en' ? /Task ended; unresolved/ : /任务已结束，仍有/)).toBeInTheDocument();
  for (const kind of ['task_session', 'job', 'launch', 'result_processing', 'unknown']) expect(container.textContent).toContain(`RAW-${kind}`);
  expect(screen.getByText(locale === 'en' ? 'Unknown execution evidence' : '未知执行证据')).toBeInTheDocument();
  expect(screen.queryByRole('button', { name: /^(Pause|Resume|暂停|恢复)$/ })).toBeNull();
});

test.each(['pausing', 'paused'] as const)('P18a explicit Resume during %s sends one request and no Pause', async control_state => {
  const calls: string[] = [];
  server.use(http.post('/api/v1/orgs/alpha/tasks/ROOT-1/:action', async ({ request, params }) => {
    calls.push(String(params.action));
    expect(await request.json()).toEqual({ expected_generation: 7 });
    return HttpResponse.json({ changed: true, pause: projection({ generation: 8 }) });
  }));
  mountRow(record({ pause: projection({ generation: 7, held: true, effective_hold: true, control_state }) }));
  fireEvent.click(screen.getByRole('button', { name: 'Resume' }));
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

test.each(errorCases)('P18a refusal %s/%s is accessible and retranslates without retry', async (status, code, english, chinese) => {
  let writes = 0;
  server.use(http.post('/api/v1/orgs/alpha/tasks/ROOT-1/pause', () => {
    writes++; return HttpResponse.json({ detail: { code } }, { status });
  }));
  mountRow();
  fireEvent.click(screen.getByRole('button', { name: 'Pause' }));
  expect(await screen.findByRole('alert')).toHaveTextContent(english);
  fireEvent.click(screen.getByTestId('test-set-locale-zh-CN'));
  expect(screen.getByRole('alert')).toHaveTextContent(chinese);
  expect(writes).toBe(1);
});

test('P18a bootstrap authentication refusal is translated and does not invent success', async () => {
  let writes = 0;
  server.use(
    http.post('/api/v1/orgs/alpha/tasks/ROOT-1/pause', () => { writes++; return new HttpResponse(null, { status: 401 }); }),
    http.get('/api/v1/auth/bootstrap', () => new HttpResponse(null, { status: 401 })),
  );
  mountRow();
  fireEvent.click(screen.getByRole('button', { name: 'Pause' }));
  expect(await screen.findByRole('alert')).toHaveTextContent('Authentication failed.');
  fireEvent.click(screen.getByTestId('test-set-locale-zh-CN'));
  expect(screen.getByRole('alert')).toHaveTextContent('身份验证失败。');
  expect(writes).toBe(1);
});


test('P18c mounted list conflict refreshes without retry or overview read; deliberate Resume uses fresh generation', async () => {
  let current = projection();
  let lists = 0; let overviews = 0;
  const writes: { action: string; body: unknown }[] = [];
  server.use(
    http.get('/api/v1/orgs', () => HttpResponse.json({ orgs: [{ slug: 'alpha', root: '/isolated-fixture' }] })),
    http.get('/api/v1/orgs/alpha/dashboard/summary', () => HttpResponse.json({ org_age_days: 1 })),
    http.get('/api/v1/orgs/alpha/tasks/roots', ({ request }) => {
      lists++;
      return HttpResponse.json({ tasks: new URL(request.url).searchParams.has('status') ? [] : [record({ pause: current })], next_cursor: null });
    }),
    // THR-292 seq41: the Tasks page no longer reads the overview; count any read as a regression.
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
  renderWithProviders(<AppRoutes />, { route: '/orgs/alpha/tasks' });
  const list = await screen.findByTestId('tasks-responsive-list');
  await within(list).findByRole('button', { name: 'Pause' });
  const previous = { lists };
  fireEvent.click(within(list).getByRole('button', { name: 'Pause' }));
  expect(await within(list).findByRole('alert')).toHaveTextContent('Pause state changed.');
  await waitFor(() => expect(screen.getAllByRole('button', { name: 'Resume' })).toHaveLength(1));
  expect(lists).toBeGreaterThan(previous.lists);
  expect(writes).toEqual([{ action: 'pause', body: { expected_generation: 6 } }]);
  fireEvent.click(within(list).getByRole('button', { name: 'Resume' }));
  await waitFor(() => expect(screen.getAllByRole('button', { name: 'Pause' })).toHaveLength(1));
  expect(writes).toEqual([{ action: 'pause', body: { expected_generation: 6 } }, { action: 'resume', body: { expected_generation: 9 } }]);
  expect(overviews).toBe(0);
});


test('P18b unavailable generation retains terminal drain diagnostics', () => {
  const unknown = { kind: 'unknown' as const, task_id: 'ROOT-1', job_id: null, owner_kind: 'host', reason: 'uncertain_tree', observed_at: '2026-10-11T00:00:00Z' };
  mountRow(record({ status: 'failed', pause: { ...projection({ held: true, blockers: [unknown], evidence_complete: false }), generation: undefined } as unknown as TaskPauseProjection }));
  expect(screen.getByText(/Pause controls unavailable/)).toBeInTheDocument();
  expect(screen.getByText(/Task ended; unresolved/)).toBeInTheDocument();
  expect(screen.getByText('Unknown execution evidence')).toBeInTheDocument();
  expect(screen.queryByRole('button', { name: 'Resume' })).toBeNull();
});
