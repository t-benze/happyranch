import { fireEvent, screen, waitFor, within } from '@testing-library/react';
import { http, HttpResponse } from 'msw';
import { beforeEach, expect, test } from 'vitest';
import { Link, Route, Routes } from 'react-router-dom';
import { renderWithProviders, savedLocaleAdapter, LocaleTestSwitch } from '@/test/render';
import { server } from '@/test/server';
import type { TaskPauseOverview as Overview, TaskPauseProjection } from '@/lib/api/types';
import { TaskPauseOverview } from './TaskPauseOverview';
import { OrgProvider } from '@/lib/orgSlug';
import { useTask, useTasksRoutes } from '@/hooks/tasks';

function projection(id: string, status: TaskPauseProjection['lifecycle_status'] = 'pending'): TaskPauseProjection {
  return { org_slug: 'alpha', root_task_id: id, lifecycle_status: status, held: false,
    effective_hold: false, generation: 2, control_state: 'unheld', paused_at: null, resumed_at: null,
    blockers: [], evidence_complete: true, observed_at: '2026-10-11T00:00:00Z' };
}
function overview(changes: Partial<Overview> = {}): Overview {
  return { org_slug: 'alpha', observed_at: '2026-10-11T00:00:00Z', evidence_complete: true,
    counts: { unheld_runnable: 0, pausing: 0, paused: 0, terminal_drain: 0, unavailable_roots: 0 },
    next_cursor: null, unheld_runnable: [], pausing: [], paused: [], terminal_drain: [], unavailable_roots: [], ...changes };
}
function OrgOverview() {
  const routes = useTasksRoutes();
  return <TaskPauseOverview key={routes.inbox()} />;
}
function mount(locale: 'en' | 'zh-CN' = 'en') {
  return renderWithProviders(<><Routes><Route path="/orgs/:slug/tasks" element={<OrgProvider><OrgOverview /></OrgProvider>} /></Routes>
    <Link to="/orgs/beta/tasks">Other organization</Link><LocaleTestSwitch to="zh-CN" /><LocaleTestSwitch to="en" /></>,
    { route: '/orgs/alpha/tasks', i18n: { adapter: savedLocaleAdapter(locale) } });
}
beforeEach(() => { sessionStorage.setItem('happyranch.token', 'test-token'); });

test('P18b loading is distinct from empty; explicit retry recovers initial error', async () => {
  let release!: () => void;
  const ready = new Promise<void>(resolve => { release = resolve; });
  let fail = true;
  server.use(http.get('/api/v1/orgs/alpha/tasks/pause-overview', async () => {
    await ready;
    return fail ? HttpResponse.json({ detail: { code: 'pause_control_unavailable' } }, { status: 409 }) : HttpResponse.json(overview());
  }));
  mount();
  expect(screen.getByText('Loading task pause overview…')).toBeInTheDocument();
  expect(screen.queryByText(/No roots in/)).toBeNull();
  release();
  expect(await screen.findByRole('alert')).toHaveTextContent('Task pause overview could not be loaded.');
  expect(screen.queryByText(/No roots in/)).toBeNull();
  fail = false;
  fireEvent.click(screen.getByRole('button', { name: 'Retry overview' }));
  expect(await screen.findByText(/No roots in these categories/)).toBeInTheDocument();
  expect(screen.queryByRole('alert')).toBeNull();
});

test.each(['en', 'zh-CN'] as const)('P18b populated/partial typed overview and pending has no Pause: %s', async locale => {
  const unknown = { kind: 'unknown' as const, task_id: 'ROOT-DRAIN', job_id: null, owner_kind: 'host', reason: 'raw_unknown', observed_at: '2026-10-11T00:00:00Z' };
  server.use(http.get('/api/v1/orgs/alpha/tasks/pause-overview', () => HttpResponse.json(overview({
    counts: null, evidence_complete: false,
    unheld_runnable: [projection('ROOT-PENDING')],
    pausing: [{ ...projection('ROOT-DRAIN', 'in_progress'), held: true, effective_hold: true, control_state: 'pausing', blockers: [unknown], evidence_complete: false }],
    paused: [{ ...projection('ROOT-HELD', 'in_progress'), held: true, effective_hold: true, control_state: 'paused' }],
    terminal_drain: [{ ...projection('ROOT-FAILED', 'failed'), held: true, blockers: [unknown], evidence_complete: false }],
    unavailable_roots: [{ ...projection('ROOT-UNKNOWN'), evidence_complete: false }],
  }))));
  mount(locale);
  const pending = (await screen.findByRole('link', { name: 'ROOT-PENDING' })).closest('li')!;
  expect(within(pending).queryByRole('button', { name: /^(Pause|暂停)$/ })).toBeNull();
  expect(screen.queryByRole('button', { name: locale === 'en' ? 'Resume' : '恢复' })).toBeNull();
  expect(screen.getByText(locale === 'en' ? 'Authoritative counts unavailable.' : '权威计数不可用。')).toBeInTheDocument();
  expect(screen.getByText(locale === 'en' ? /Partial page./ : /部分页面。/)).toBeInTheDocument();
  expect(screen.getByText(locale === 'en' ? /Task ended; unresolved/ : /任务已结束，仍有/)).toBeInTheDocument();
  expect(screen.getAllByText(locale === 'en' ? 'Unknown execution evidence' : '未知执行证据')).toHaveLength(2);
});

test('P18b opaque pagination replaces observation, retains null counts and resets on org switch', async () => {
  const cursors: (string | null)[] = [];
  server.use(http.get('/api/v1/orgs/:slug/tasks/pause-overview', ({ request, params }) => {
    const cursor = new URL(request.url).searchParams.get('before'); cursors.push(cursor);
    if (params.slug === 'beta') return HttpResponse.json(overview({ org_slug: 'beta', unheld_runnable: [{ ...projection('BETA-PENDING'), org_slug: 'beta' }] }));
    return HttpResponse.json(cursor ? overview({ counts: null, observed_at: '2026-10-11T01:00:00Z', unheld_runnable: [projection('SECOND')] }) : overview({ counts: null, next_cursor: 'opaque:/+cursor=', unheld_runnable: [projection('FIRST')] }));
  }));
  mount();
  await screen.findByRole('link', { name: 'FIRST' });
  fireEvent.click(screen.getByRole('button', { name: 'Next observation page' }));
  await screen.findByRole('link', { name: 'SECOND' });
  expect(screen.queryByRole('link', { name: 'FIRST' })).toBeNull();
  expect(screen.getByText(/Authoritative counts unavailable/)).toBeInTheDocument();
  expect(cursors).toEqual([null, 'opaque:/+cursor=']);
  fireEvent.click(screen.getByRole('link', { name: 'Other organization' }));
  await screen.findByRole('link', { name: 'BETA-PENDING' });
  expect(screen.queryByRole('link', { name: 'SECOND' })).toBeNull();
  expect(cursors).toEqual([null, 'opaque:/+cursor=', null]);
});

test('P18b stale refresh preserves earlier observation and explicit Retry', async () => {
  let fail = false;
  server.use(http.get('/api/v1/orgs/alpha/tasks/pause-overview', () => fail
    ? HttpResponse.json({ detail: 'raw_read_error' }, { status: 503 })
    : HttpResponse.json(overview({ unheld_runnable: [projection('RETAINED')] }))));
  mount();
  await screen.findByRole('link', { name: 'RETAINED' });
  fail = true;
  fireEvent.click(screen.getByRole('button', { name: 'Refresh overview' }));
  expect(await screen.findByRole('alert')).toHaveTextContent('Stale observation: refresh failed.');
  expect(screen.getByRole('link', { name: 'RETAINED' })).toBeInTheDocument();
  expect(screen.getByRole('button', { name: 'Retry overview' })).toBeInTheDocument();
});

test('P18d locale switch preserves focused overview button with zero requests', async () => {
  let reads = 0;
  server.use(http.get('/api/v1/orgs/alpha/tasks/pause-overview', () => { reads++; return HttpResponse.json(overview()); }));
  mount();
  await screen.findByText(/No roots in/);
  const button = screen.getByRole('button', { name: 'Refresh overview' }); button.focus();
  const before = reads;
  fireEvent.click(screen.getByTestId('test-set-locale-zh-CN'));
  expect(screen.getByRole('button', { name: '刷新概览' })).toBe(button);
  expect(document.activeElement).toBe(button);
  expect(reads).toBe(before);
  fireEvent.click(screen.getByTestId('test-set-locale-en'));
  expect(screen.getByRole('button', { name: 'Refresh overview' })).toBe(button);
  expect(reads).toBe(before);
});

// Actual hook consumer used by the app; this test mount adds no production seam.
function DetailProjection() {
  const task = useTask('ROOT-1');
  return <output aria-label="Root projection">{task.data?.pause?.root_task_id} / {task.data?.pause?.generation}</output>;
}
test('P18c detail envelope projection wins embedded record and survives remount; org switch clears old data', async () => {
  server.use(http.get('/api/v1/orgs/:slug/tasks/ROOT-1', ({ params }) => HttpResponse.json({
    task: { task_id: 'ROOT-1', pause: projection('STALE') }, pause: { ...projection(String(params.slug)), generation: 7 },
  })));
  const ui = <><Routes><Route path="/orgs/:slug/tasks" element={<DetailProjection />} /></Routes><Link to="/orgs/beta/tasks">Other organization</Link></>;
  const options = { route: '/orgs/alpha/tasks' };
  const first = renderWithProviders(ui, options);
  await waitFor(() => expect(screen.getByLabelText('Root projection')).toHaveTextContent('alpha / 7'));
  first.unmount();
  renderWithProviders(ui, options);
  await waitFor(() => expect(screen.getByLabelText('Root projection')).toHaveTextContent('alpha / 7'));
  fireEvent.click(screen.getByRole('link', { name: 'Other organization' }));
  expect(screen.getByLabelText('Root projection')).not.toHaveTextContent('alpha');
  await waitFor(() => expect(screen.getByLabelText('Root projection')).toHaveTextContent('beta / 7'));
});
