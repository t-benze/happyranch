import { act, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { http, HttpResponse } from 'msw';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, beforeEach, test, expect, vi } from 'vitest';
import { AppProvider, makeQueryClient } from '@/design-system/providers/AppProvider';
import { AppRoutes } from '@/routes';
import { I18nTestBoundary } from '@/test/render';
import { server } from '@/test/server';

// The ordinary status=escalated owner sends the same wire request as the
// attention owner; a sentinel that never intersects keeps it at its first page
// so every continuation request below belongs to the attention owner.
beforeEach(() => {
  vi.stubGlobal('IntersectionObserver', class { observe() {} disconnect() {} unobserve() {} });
});
afterEach(() => { vi.unstubAllGlobals(); });

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
      if (p.get('status') !== 'escalated') return HttpResponse.json({ tasks: [], next_cursor: null });
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
      if (params.status !== 'escalated') return HttpResponse.json({ tasks: [], next_cursor: null });
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
  // Two identical first-page requests: the ordinary escalated owner and the
  // attention owner each own one. Only attention continued, with its own cursor.
  expect(attentionRequests.filter((params) => !params.before)).toEqual([
    { status: 'escalated', limit: '50' },
    { status: 'escalated', limit: '50' },
  ]);
  expect(attentionRequests.filter((params) => params.before)).toEqual([
    { status: 'escalated', limit: '50', before: 'attention-cursor' },
    { status: 'escalated', limit: '50', before: 'attention-cursor' },
  ]);
  await qc.cancelQueries();
  qc.clear();
});

// Global End of list requires the attention owner, too, to have succeeded and
// been exhausted with no visible rows. The ordinary escalated owner is seeded
// exhausted (it shares the wire request), so every status=escalated request
// below belongs to the attention owner while the other ordinary owners settle
// to one exhausted completed row.
test.each([
  ['more', false], ['pending', false], ['initial-error', false], ['continuation-error', false],
  ['stale-refresh', false], ['exhausted-visible', false], ['exhausted-empty', true],
] as const)('End of list with exhausted ordinary owners and attention %s: shown=%s', async (mode, shown) => {
  sessionStorage.setItem('happyranch.token', 'tok');
  const row = { team: 'content', block_kind: null, parent_task_id: null, revisit_of_task_id: null,
    created_at: '2026-05-18T10:00:00Z', updated_at: '2026-05-18T10:06:12Z',
    closed_at: null, cancelled_at: null, session_timeout_seconds: null };
  const done = { ...row, task_id: 'TASK-DONE', brief: 'Exhausted completed root', status: 'completed', severity_rollup: 'completed' };
  const esc = (i: number) => ({ ...row, task_id: `TASK-ESC-${i}`, brief: `Waiting decision ${i}`, status: 'escalated', severity_rollup: 'escalated' });
  let failAttention = false;
  let releasePending = () => {};
  server.use(
    http.get('/api/v1/orgs', () => HttpResponse.json({ orgs: [{ slug: 'review-org', root: '/x' }] })),
    http.get('/api/v1/orgs/:slug/dashboard/summary', () => HttpResponse.json({ org_age_days: 1 })),
    http.get('/api/v1/orgs/review-org/agents', () => HttpResponse.json({ agents: [] })),
    http.get('/api/v1/orgs/review-org/tasks/roots', async ({ request }) => {
      const p = new URL(request.url).searchParams;
      if (p.get('status') !== 'escalated') {
        return HttpResponse.json({ tasks: p.get('status') === 'completed' ? [done] : [], next_cursor: null });
      }
      if (mode === 'pending') await new Promise<void>((resolve) => { releasePending = resolve; });
      if (failAttention || mode === 'initial-error' || p.has('before')) return new HttpResponse(null, { status: 500 });
      if (mode === 'more' || mode === 'continuation-error') return HttpResponse.json({ tasks: Array.from({ length: 50 }, (_, i) => esc(i)), next_cursor: 'TASK-ESC-49' });
      return HttpResponse.json({ tasks: mode === 'exhausted-empty' ? [] : [esc(0)], next_cursor: null });
    }),
  );
  const qc = makeQueryClient();
  qc.setDefaultOptions({ queries: { retry: false } });
  const ordinaryEscalated = ['tasks-list-ordinary', 'review-org', { status: 'escalated' }];
  qc.setQueryDefaults(ordinaryEscalated, { staleTime: Infinity });
  qc.setQueryData(ordinaryEscalated, { pages: [{ tasks: [], next_cursor: null }], pageParams: [undefined] });
  render(<MemoryRouter initialEntries={['/orgs/review-org/tasks']}><AppProvider client={qc}><I18nTestBoundary><AppRoutes /></I18nTestBoundary></AppProvider></MemoryRouter>);
  try {
    await screen.findByText('Exhausted completed root');
    // Every ordinary owner has succeeded and is exhausted.
    await waitFor(() => {
      for (const status of ['pending', 'in_progress', 'escalated', 'completed', 'failed', 'cancelled', 'superseded']) {
        const data = qc.getQueryData<{ pages: { next_cursor: string | null }[] }>(['tasks-list-ordinary', 'review-org', { status }]);
        expect(data?.pages.at(-1)?.next_cursor).toBeNull();
      }
    });
    if (mode === 'more') await screen.findByText('50+ waiting on you');
    if (mode === 'pending') await screen.findByText('Loading waiting-on-you tasks…');
    if (mode === 'initial-error') await screen.findByText('Could not load waiting-on-you tasks');
    if (mode === 'continuation-error') {
      await screen.findByText('50+ waiting on you');
      await userEvent.click(screen.getByRole('button', { name: 'Load more waiting-on-you tasks' }));
      await screen.findByRole('button', { name: 'Retry loading more waiting-on-you tasks' });
    }
    if (mode === 'stale-refresh' || mode === 'exhausted-visible') await screen.findByText('1 waiting on you');
    if (mode === 'stale-refresh') {
      failAttention = true;
      await act(async () => { await qc.invalidateQueries({ queryKey: ['tasks-roots-infinite', 'review-org', { status: 'escalated' }], exact: true }); });
      await screen.findByText('Could not load waiting-on-you tasks; previously loaded rows are still shown.');
      expect(screen.getByText('Waiting decision 0')).toBeInTheDocument();
    }
    if (shown) {
      expect(await screen.findByText('End of list')).toBeInTheDocument();
    } else {
      expect(screen.queryByText('End of list')).not.toBeInTheDocument();
      expect(screen.queryByText('No tasks')).not.toBeInTheDocument();
    }
  } finally {
    releasePending();
    await qc.cancelQueries();
    qc.clear();
  }
});
