import { act, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { http, HttpResponse } from 'msw';
import { MemoryRouter } from 'react-router-dom';
import { test, expect } from 'vitest';
import { AppProvider, makeQueryClient } from '@/design-system/providers/AppProvider';
import { AppRoutes } from '@/routes';
import { I18nTestBoundary } from '@/test/render';
import { server } from '@/test/server';

test.each(['pagination', 'refresh'])('retains loaded attention rows after %s failure', async (mode) => {
  sessionStorage.setItem('happyranch.token', 'tok');
  const task = { task_id: 'TASK-ESC', team: 'content', brief: 'Retained founder decision', status: 'escalated',
    block_kind: null, parent_task_id: null, revisit_of_task_id: null,
    created_at: '2026-05-18T10:00:00Z', updated_at: '2026-05-18T10:06:12Z',
    closed_at: null, cancelled_at: null, session_timeout_seconds: null, severity_rollup: 'escalated' };
  let fail = false;
  const requests: string[] = [];
  server.use(
    http.get('/api/v1/orgs', () => HttpResponse.json({ orgs: [{ slug: 'review-org', root: '/x' }] })),
    http.get('/api/v1/orgs/:slug/dashboard/summary', () => HttpResponse.json({ org_age_days: 1 })),
    http.get('/api/v1/orgs/review-org/tasks/roots', ({ request }) => {
      const p = new URL(request.url).searchParams;
      if (!p.has('status')) return HttpResponse.json({ tasks: [task], next_cursor: null });
      requests.push(p.toString());
      if (fail || p.has('before')) return new HttpResponse(null, { status: 500 });
      const tasks = mode === 'pagination' ? Array.from({ length: 50 }, (_, i) => i === 0 ? task : { ...task, task_id: `TASK-ESC-${i}`, brief: `Other decision ${i}` }) : [task];
      return HttpResponse.json({ tasks, next_cursor: mode === 'pagination' ? 'TASK-ESC-49' : null });
    }),
  );
  const qc = makeQueryClient();
  qc.setDefaultOptions({ queries: { retry: false } });
  render(<MemoryRouter initialEntries={['/orgs/review-org/tasks']}><AppProvider client={qc}><I18nTestBoundary><AppRoutes /></I18nTestBoundary></AppProvider></MemoryRouter>);
  await screen.findByRole('heading', { name: 'Waiting on you' });
  expect(screen.getAllByText('Retained founder decision')).toHaveLength(1);
  if (mode === 'pagination') {
    await userEvent.click(screen.getByRole('button', { name: 'Load more waiting-on-you tasks' }));
  } else {
    fail = true;
    await act(async () => { await qc.invalidateQueries({ queryKey: ['tasks-roots-infinite', 'review-org', { status: 'escalated' }], exact: true }); });
  }
  await waitFor(() => expect(screen.getByRole('alert')).toHaveTextContent('Could not load waiting-on-you tasks'));
  try {
    expect(screen.queryByText('Retained founder decision')).toBeInTheDocument();
  } finally {
    await qc.cancelQueries();
    qc.clear();
  }
});

test('retries a failed attention continuation with its own cursor and reaches the final count', async () => {
  sessionStorage.setItem('happyranch.token', 'tok');
  const first = { task_id: 'TASK-ESC-FIRST', team: 'content', brief: 'First retained founder decision', status: 'escalated',
    block_kind: null, parent_task_id: null, revisit_of_task_id: null,
    created_at: '2026-05-18T10:00:00Z', updated_at: '2026-05-18T10:06:12Z',
    closed_at: null, cancelled_at: null, session_timeout_seconds: null, severity_rollup: 'escalated' };
  const final = { ...first, task_id: 'TASK-ESC-FINAL', brief: 'Final retried founder decision' };
  const attentionRequests: Record<string, string>[] = [];
  let continuationAttempts = 0;
  server.use(
    http.get('/api/v1/orgs', () => HttpResponse.json({ orgs: [{ slug: 'review-org', root: '/x' }] })),
    http.get('/api/v1/orgs/:slug/dashboard/summary', () => HttpResponse.json({ org_age_days: 1 })),
    http.get('/api/v1/orgs/review-org/tasks/roots', ({ request }) => {
      const params = Object.fromEntries(new URL(request.url).searchParams);
      if (params.status !== 'escalated') return HttpResponse.json({ tasks: [first], next_cursor: null });
      attentionRequests.push(params);
      if (!params.before) return HttpResponse.json({ tasks: Array.from({ length: 50 }, (_, i) => i === 0 ? first : { ...first, task_id: `TASK-ESC-${i}`, brief: `Other decision ${i}` }), next_cursor: 'attention-cursor' });
      continuationAttempts += 1;
      return continuationAttempts === 1
        ? new HttpResponse(null, { status: 500 })
        : HttpResponse.json({ tasks: [final], next_cursor: null });
    }),
  );
  const qc = makeQueryClient();
  qc.setDefaultOptions({ queries: { retry: false } });
  render(<MemoryRouter initialEntries={['/orgs/review-org/tasks']}><AppProvider client={qc}><I18nTestBoundary><AppRoutes /></I18nTestBoundary></AppProvider></MemoryRouter>);
  await screen.findByText('50+ waiting on you');
  await userEvent.click(screen.getByRole('button', { name: 'Load more waiting-on-you tasks' }));
  const alert = await screen.findByRole('alert');
  expect(screen.getAllByText('First retained founder decision')).toHaveLength(1);
  await userEvent.click(within(alert).getByRole('button', { name: 'Retry loading more waiting-on-you tasks' }));
  await screen.findByText('Final retried founder decision');
  expect(screen.getByText('51 waiting on you')).toBeInTheDocument();
  expect(attentionRequests).toEqual([
    { status: 'escalated', limit: '50' },
    { status: 'escalated', limit: '50', before: 'attention-cursor' },
    { status: 'escalated', limit: '50', before: 'attention-cursor' },
  ]);
  await qc.cancelQueries();
  qc.clear();
});
