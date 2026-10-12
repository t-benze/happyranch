import { fireEvent, screen, waitFor, within } from '@testing-library/react';
import { http, HttpResponse } from 'msw';
import { beforeEach, expect, test } from 'vitest';
import { Link, Route, Routes } from 'react-router-dom';
import { renderWithProviders, savedLocaleAdapter, LocaleTestSwitch } from '@/test/render';
import { server } from '@/test/server';
import type { TaskPauseProjection, TaskRecord } from '@/lib/api/types';
import { AppRoutes } from '@/routes';
import { useTask } from '@/hooks/tasks';
import { en } from '@/lib/i18n/locales/en';
import { zhCN } from '@/lib/i18n/locales/zh-CN';

function projection(id: string, status: TaskPauseProjection['lifecycle_status'] = 'pending'): TaskPauseProjection {
  return { org_slug: 'alpha', root_task_id: id, lifecycle_status: status, held: false,
    effective_hold: false, generation: 2, control_state: 'unheld', paused_at: null, resumed_at: null,
    blockers: [], evidence_complete: true, observed_at: '2026-10-11T00:00:00Z' };
}
function root(id: string, pause: TaskPauseProjection | undefined, changes: Partial<TaskRecord> = {}): TaskRecord {
  return { task_id: id, team: 'engineering', brief: `Brief ${id}`, status: 'in_progress', block_kind: null,
    parent_task_id: null, revisit_of_task_id: null, created_at: '2026-10-11T00:00:00Z', updated_at: '2026-10-11T00:00:00Z',
    closed_at: null, cancelled_at: null, session_timeout_seconds: null, assigned_agent: 'agent-a', pause, ...changes } as TaskRecord;
}
const EMPTY_OVERVIEW = { org_slug: 'alpha', observed_at: '2026-10-11T00:00:00Z', evidence_complete: true, counts: null, next_cursor: null, unheld_runnable: [], pausing: [], paused: [], terminal_drain: [], unavailable_roots: [] };
const blocker = (kind: 'task_session' | 'unknown') => ({ kind, task_id: `RAW-${kind}`, job_id: null, owner_kind: 'raw-owner', reason: 'raw_reason', observed_at: '2026-10-11T00:00:00Z' });
// Every pause shape the list could previously render: unheld/pausing/paused roots, pending,
// escalated held, terminal with drain blockers, missing projection and an inherited child.
const ROOTS = [
  root('ROOT-UNHELD', projection('ROOT-UNHELD', 'in_progress')),
  root('ROOT-PAUSING', { ...projection('ROOT-PAUSING', 'in_progress'), held: true, effective_hold: true, control_state: 'pausing', evidence_complete: false, blockers: [blocker('task_session')] }),
  root('ROOT-PAUSED', { ...projection('ROOT-PAUSED', 'in_progress'), held: true, effective_hold: true, control_state: 'paused' }),
  root('ROOT-PENDING', projection('ROOT-PENDING'), { status: 'pending' }),
  root('ROOT-ESCALATED', { ...projection('ROOT-ESCALATED', 'escalated'), held: true, effective_hold: true, control_state: 'paused' }, { status: 'escalated' }),
  root('ROOT-FAILED', { ...projection('ROOT-FAILED', 'failed'), held: true, evidence_complete: false, blockers: [blocker('unknown')] }, { status: 'failed' }),
  root('ROOT-MISSING', undefined),
  root('CHILD-9', { ...projection('ROOT-PAUSED', 'in_progress'), held: true, effective_hold: true, control_state: 'paused' }, { parent_task_id: 'ROOT-PAUSED' }),
];
// Distinctive rendered pause copy per locale (placeholder-free prefixes), sourced from the shipped catalogs.
const PAUSE_COPY_KEYS = ['tasks.pause.unheld', 'tasks.pause.pausing', 'tasks.pause.paused', 'tasks.pause.unavailable',
  'tasks.pause.inherited', 'tasks.pause.evidenceComplete', 'tasks.pause.evidenceIncomplete', 'tasks.pause.observed',
  'tasks.pause.terminalDrain', 'tasks.pause.blocker.task', 'tasks.pause.blocker.unknown'] as const;
function pauseCopy(locale: 'en' | 'zh-CN'): string[] {
  const catalog = (locale === 'en' ? en : zhCN) as Record<string, unknown>;
  return PAUSE_COPY_KEYS.map(key => String(catalog[key]).split('{')[0].trim()).filter(Boolean);
}
// Ordinary Tasks route mounts no pause UI at all (founder THR-292 seq41 overview + seq45 rows).
// The callable GET /tasks/pause-overview client contract survives separately (lib/api/tasks.test.ts).
function mountTasks(locale: 'en' | 'zh-CN', route = '/orgs/alpha/tasks') {
  const requests: string[] = [];
  const writes: string[] = [];
  server.use(
    http.get('/api/v1/orgs', () => HttpResponse.json({ orgs: [{ slug: 'alpha', root: '/isolated-fixture' }] })),
    http.get('/api/v1/orgs/alpha/dashboard/summary', () => HttpResponse.json({ org_age_days: 1 })),
    http.get('/api/v1/orgs/alpha/tasks/roots', ({ request }) => {
      const status = new URL(request.url).searchParams.get('status');
      requests.push(`roots${new URL(request.url).search}`);
      return HttpResponse.json({ tasks: status ? ROOTS.filter(t => t.status === status) : ROOTS, next_cursor: null });
    }),
    // Valid empty observation, so a regressed panel renders (and is observable) rather than crashing.
    http.get('/api/v1/orgs/alpha/tasks/pause-overview', () => { requests.push('pause-overview'); return HttpResponse.json(EMPTY_OVERVIEW); }),
    http.get('/api/v1/orgs/alpha/jobs/', () => { requests.push('jobs'); return HttpResponse.json({ jobs: [], next_cursor: null }); }),
    http.get('/api/v1/orgs/alpha/tasks/:id', ({ params }) => {
      requests.push(`detail ${params.id}`);
      const task = ROOTS.find(t => t.task_id === params.id);
      return HttpResponse.json({ task, pause: task?.pause, results: [], audit_log: [], revisit_chain: [], direct_revisits: [], predecessor_prior_status: null });
    }),
    http.get('/api/v1/orgs/alpha/tasks/:id/recall', ({ params }) => {
      requests.push(`recall ${params.id}`);
      return HttpResponse.json({ task_id: params.id, assigned_agent: 'agent-a', brief: `Brief ${params.id}`, status: 'in_progress', output_summary: null, children: [] });
    }),
    http.get('/api/v1/orgs/alpha/tasks/:id/events', () => HttpResponse.text('', { headers: { 'content-type': 'text/event-stream' } })),
    http.post('/api/v1/orgs/alpha/tasks/:id/:action', ({ params }) => {
      writes.push(`${params.action} ${params.id}`);
      return HttpResponse.json({ changed: true, pause: { ...projection(String(params.id), 'in_progress'), held: true, effective_hold: true, control_state: 'paused', generation: 3 } });
    }),
  );
  renderWithProviders(<><AppRoutes /><LocaleTestSwitch to="zh-CN" /><LocaleTestSwitch to="en" /></>,
    { route, i18n: { adapter: savedLocaleAdapter(locale) } });
  return { requests, writes };
}
async function expectListWithoutPause(locale: 'en' | 'zh-CN') {
  const list = await screen.findByTestId('tasks-responsive-list');
  await within(list).findByRole('link', { name: /Brief ROOT-FAILED/ });
  const main = document.body;
  // Ordinary lifecycle badges and row navigation stay intact.
  for (const status of ['in_progress', 'pending', 'escalated', 'failed']) expect(within(main).getAllByText(status).length).toBeGreaterThan(0);
  for (const task of ROOTS) expect(within(main).getAllByRole('link', { name: new RegExp(`Brief ${task.task_id}`) })[0]).toHaveAttribute('href', `/orgs/alpha/tasks/${task.task_id}`);
  // No pause controls, state labels, evidence, blockers, unavailable or inherited explanation anywhere on the page.
  expect(document.querySelector('[data-task-pause]')).toBeNull();
  expect(screen.queryByRole('button', { name: /^(Pause|Resume|暂停|恢复)$/ })).toBeNull();
  for (const copy of [...pauseCopy('en'), ...pauseCopy('zh-CN')]) expect(main.textContent).not.toContain(copy);
  for (const raw of ['RAW-task_session', 'RAW-unknown']) expect(main.textContent).not.toContain(raw);
  expect(main.querySelector('details')).toBeNull();
  for (const name of ['Task pause overview', '任务暂停概览']) {
    expect(screen.queryByRole('region', { name })).toBeNull();
    expect(screen.queryByRole('heading', { name })).toBeNull();
  }
  for (const name of [/overview/i, /概览/, /observation page/i, /观测页/]) expect(screen.queryByRole('button', { name })).toBeNull();
  expect(document.documentElement.lang).toBe(locale);
}
beforeEach(() => { sessionStorage.setItem('happyranch.token', 'test-token'); });

test.each(['en', 'zh-CN'] as const)('THR-292 seq41+45 Tasks list renders no pause UI or overview read; lifecycle and navigation remain: %s', async locale => {
  const { requests } = mountTasks(locale);
  await expectListWithoutPause(locale);
  expect(requests.some(r => r.startsWith('roots?') && r.includes('status=escalated'))).toBe(true);
  expect(requests).not.toContain('pause-overview');
});

test('THR-292 seq45 list -> detail keeps root controls on detail; list stays pause-free after action, navigation and locale switches', async () => {
  const { requests, writes } = mountTasks('en');
  await expectListWithoutPause('en');
  fireEvent.click(within(screen.getByTestId('tasks-responsive-list')).getByRole('link', { name: /Brief ROOT-UNHELD/ }));
  const pause = await screen.findByRole('button', { name: 'Pause' });
  expect(pause.closest('[data-task-pause="ROOT-UNHELD"]')).not.toBeNull();
  fireEvent.click(pause);
  await waitFor(() => expect(writes).toEqual(['pause ROOT-UNHELD']));
  fireEvent.click(screen.getByRole('link', { name: en['tasks.detail.back'] }));
  await expectListWithoutPause('en');
  fireEvent.click(screen.getByTestId('test-set-locale-zh-CN'));
  await expectListWithoutPause('zh-CN');
  fireEvent.click(screen.getByTestId('test-set-locale-en'));
  await expectListWithoutPause('en');
  expect(writes).toEqual(['pause ROOT-UNHELD']);
  expect(requests).not.toContain('pause-overview');
});

test('THR-292 seq45 detail locale switch keeps the focused root Pause node with zero requests and no overview', async () => {
  const { requests, writes } = mountTasks('en', '/orgs/alpha/tasks/ROOT-UNHELD');
  const pause = await screen.findByRole('button', { name: 'Pause' });
  expect(pause.closest('[data-task-pause="ROOT-UNHELD"]')).not.toBeNull();
  await waitFor(() => expect(requests).toContain('jobs'));
  pause.focus();
  expect(document.activeElement).toBe(pause);
  const before = requests.length;
  // Focus is set once only; each switch must keep it on the same root Pause node without repair.
  fireEvent.click(screen.getByTestId('test-set-locale-zh-CN'));
  expect(document.activeElement).toBe(pause);
  expect(screen.getByRole('button', { name: '暂停' })).toBe(pause);
  fireEvent.click(screen.getByTestId('test-set-locale-en'));
  expect(document.activeElement).toBe(pause);
  expect(screen.getByRole('button', { name: 'Pause' })).toBe(pause);
  expect(requests.length).toBe(before);
  expect(writes).toEqual([]);
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
