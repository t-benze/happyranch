import { fireEvent, screen, waitFor, within } from '@testing-library/react';
import { http, HttpResponse } from 'msw';
import { beforeEach, expect, test } from 'vitest';
import { Link, Route, Routes } from 'react-router-dom';
import { renderWithProviders, savedLocaleAdapter, LocaleTestSwitch } from '@/test/render';
import { server } from '@/test/server';
import type { TaskPauseProjection, TaskRecord } from '@/lib/api/types';
import { AppRoutes } from '@/routes';
import { useTask } from '@/hooks/tasks';

function projection(id: string, status: TaskPauseProjection['lifecycle_status'] = 'pending'): TaskPauseProjection {
  return { org_slug: 'alpha', root_task_id: id, lifecycle_status: status, held: false,
    effective_hold: false, generation: 2, control_state: 'unheld', paused_at: null, resumed_at: null,
    blockers: [], evidence_complete: true, observed_at: '2026-10-11T00:00:00Z' };
}
function root(id: string, pause: TaskPauseProjection): TaskRecord {
  return { task_id: id, team: 'engineering', brief: `Brief ${id}`, status: 'in_progress', block_kind: null,
    parent_task_id: null, revisit_of_task_id: null, created_at: '2026-10-11T00:00:00Z', updated_at: '2026-10-11T00:00:00Z',
    closed_at: null, cancelled_at: null, session_timeout_seconds: null, assigned_agent: 'agent-a', pause } as TaskRecord;
}
const EMPTY_OVERVIEW = { org_slug: 'alpha', observed_at: '2026-10-11T00:00:00Z', evidence_complete: true, counts: null, next_cursor: null, unheld_runnable: [], pausing: [], paused: [], terminal_drain: [], unavailable_roots: [] };
const ROOTS = [
  root('ROOT-UNHELD', projection('ROOT-UNHELD', 'in_progress')),
  root('ROOT-PAUSING', { ...projection('ROOT-PAUSING', 'in_progress'), held: true, effective_hold: true, control_state: 'pausing', evidence_complete: false }),
  root('ROOT-PAUSED', { ...projection('ROOT-PAUSED', 'in_progress'), held: true, effective_hold: true, control_state: 'paused' }),
];
// Ordinary Tasks route no longer mounts the standalone pause overview (founder THR-292 seq41).
// The callable GET /tasks/pause-overview client contract survives separately (lib/api/tasks.test.ts).
function mountTasks(locale: 'en' | 'zh-CN') {
  const requests: string[] = [];
  server.use(
    http.get('/api/v1/orgs', () => HttpResponse.json({ orgs: [{ slug: 'alpha', root: '/isolated-fixture' }] })),
    http.get('/api/v1/orgs/alpha/dashboard/summary', () => HttpResponse.json({ org_age_days: 1 })),
    http.get('/api/v1/orgs/alpha/tasks/roots', ({ request }) => {
      requests.push(`roots${new URL(request.url).search}`);
      return HttpResponse.json({ tasks: new URL(request.url).searchParams.has('status') ? [] : ROOTS, next_cursor: null });
    }),
    // Valid empty observation, so a regressed panel renders (and is observable) rather than crashing.
    http.get('/api/v1/orgs/alpha/tasks/pause-overview', () => { requests.push('pause-overview'); return HttpResponse.json(EMPTY_OVERVIEW); }),
  );
  renderWithProviders(<><AppRoutes /><LocaleTestSwitch to="zh-CN" /><LocaleTestSwitch to="en" /></>,
    { route: '/orgs/alpha/tasks', i18n: { adapter: savedLocaleAdapter(locale) } });
  return requests;
}
beforeEach(() => { sessionStorage.setItem('happyranch.token', 'test-token'); });

test.each(['en', 'zh-CN'] as const)('THR-292 seq41 Tasks route has no pause overview panel or overview read; root controls remain: %s', async locale => {
  const requests = mountTasks(locale);
  const list = await screen.findByTestId('tasks-responsive-list');
  await within(list).findByText(locale === 'en' ? 'Pausing' : '暂停中');
  expect(within(list).getByText(locale === 'en' ? 'Paused' : '已暂停')).toBeInTheDocument();
  expect(within(list).getByRole('button', { name: locale === 'en' ? 'Pause' : '暂停' })).toBeInTheDocument();
  expect(within(list).getAllByRole('button', { name: locale === 'en' ? 'Resume' : '恢复' })).toHaveLength(2);
  for (const name of ['Task pause overview', '任务暂停概览']) {
    expect(screen.queryByRole('region', { name })).toBeNull();
    expect(screen.queryByRole('heading', { name })).toBeNull();
  }
  for (const name of [/overview/i, /概览/, /observation page/i, /观测页/]) expect(screen.queryByRole('button', { name })).toBeNull();
  expect(requests).not.toContain('pause-overview');
});

test('THR-292 seq41 locale switch keeps focused root Pause control with zero requests and no overview', async () => {
  const requests = mountTasks('en');
  const list = await screen.findByTestId('tasks-responsive-list');
  const pause = await within(list).findByRole('button', { name: 'Pause' });
  pause.focus();
  const before = requests.length;
  fireEvent.click(screen.getByTestId('test-set-locale-zh-CN'));
  expect(within(list).getByRole('button', { name: '暂停' })).toBe(pause);
  fireEvent.click(screen.getByTestId('test-set-locale-en'));
  expect(within(list).getByRole('button', { name: 'Pause' })).toBe(pause);
  pause.focus();
  expect(document.activeElement).toBe(pause);
  expect(requests.length).toBe(before);
  expect(requests).not.toContain('pause-overview');
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
